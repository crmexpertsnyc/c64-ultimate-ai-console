"""Streams (MJPEG video, PCM audio over WebSocket) and the live event WebSocket."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse

from app.container import Container
from app.ultimate.client import UltimateError
from app.ultimate.streams import AUDIO_RATE_PAL, render_text_frame
from app.ultimate.telnet import TelnetFilter, escape_outgoing, open_telnet

from .deps import get_container, get_source

router = APIRouter(tags=["streams"])
log = logging.getLogger("c64.api.streams")
BOUNDARY = "c64frame"


@router.get("/api/streams")
async def streams_status(c: Container = Depends(get_container)):
    return {**c.device.streams.status(), "simulated": c.device.simulator is not None,
            "targetHost": None if c.device.simulator else c.device.stream_target_host()}


@router.post("/api/streams/{kind}/{action}")
async def stream_control(kind: Literal["video", "audio"], action: Literal["start", "stop"],
                         c: Container = Depends(get_container), source: str = Depends(get_source)):
    try:
        async with c.audit.action(source, f"streams.{kind}.{action}") as rec:
            result = await (c.device.start_stream(kind) if action == "start" else c.device.stop_stream(kind))
            rec.set_response(result)
            return result
    except UltimateError as exc:
        raise HTTPException(502, str(exc)) from exc


def _current_jpeg(c: Container) -> bytes | None:
    if c.device.simulator is not None:
        return render_text_frame(c.device.simulator.lines)
    return c.device.streams.latest_jpeg()


@router.get("/api/streams/video/frame.jpg", summary="Latest decoded frame")
async def frame(c: Container = Depends(get_container)):
    jpeg = _current_jpeg(c)
    if jpeg is None:
        raise HTTPException(404, "no frame received yet — start the video stream")
    return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


async def _pace(last_sent: float, fps: int) -> float:
    """Cap the send rate without delaying frames: sleep only for whatever is left of the frame
    interval (waiting for the next frame from the C64 usually uses it up already)."""
    import time
    remaining = 1 / fps - (time.monotonic() - last_sent)
    if remaining > 0:
        await asyncio.sleep(remaining)
    return time.monotonic()


@router.get("/api/streams/video.mjpeg", summary="MJPEG stream for an <img> tag")
async def mjpeg(c: Container = Depends(get_container), fps: int = 25):
    fps = max(1, min(fps, 50))

    async def gen():
        last = -1
        last_sent = 0.0
        while True:
            if c.device.simulator is not None:
                await asyncio.sleep(0.2)
                jpeg = _current_jpeg(c)
            else:
                video = c.device.streams.video
                try:
                    await asyncio.wait_for(video.new_frame.wait(), timeout=2.0)
                except TimeoutError:
                    continue
                if video.frames == last:
                    continue
                last = video.frames
                jpeg = await asyncio.to_thread(c.device.streams.latest_jpeg)
            if jpeg:
                yield (f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n").encode() \
                    + jpeg + b"\r\n"
                last_sent = await _pace(last_sent, fps)

    return StreamingResponse(gen(), media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
                             headers={"Cache-Control": "no-store"})


@router.websocket("/ws/video")
async def video_ws(ws: WebSocket, fps: int = 50):
    """Latest-frame video with timing metadata, for the Display page canvas and latency readout.

    Each binary message = 4-byte little-endian JSON length + JSON metadata + JPEG. Stale frames are
    skipped, never queued. Text messages {"type": "ping", "t": <client ms>} are answered with
    {"type": "pong", "t": ..., "server": <server ms>} so the browser can correct for clock offset.
    """
    import json
    import struct
    import time

    from app.ultimate.streams import frame_to_jpeg

    c: Container = ws.app.state.container
    await ws.accept()
    fps = max(1, min(fps, 50))
    video = c.device.streams.video
    try:
        await c.device.join_stream("video")
    except (UltimateError, ValueError) as exc:
        await ws.send_json({"type": "error", "detail": str(exc)})
        await ws.close()
        return

    async def pinger():
        while True:
            msg = await ws.receive_json()
            if isinstance(msg, dict) and msg.get("type") == "ping":
                await ws.send_json({"type": "pong", "t": msg.get("t"), "server": time.time() * 1000})

    reader = asyncio.create_task(pinger())
    last = -1
    last_sent = 0.0
    try:
        while not reader.done():
            if c.device.simulator is not None:
                await asyncio.sleep(0.2)
                t0 = time.time()
                jpeg = await asyncio.to_thread(render_text_frame, c.device.simulator.lines)
                meta = {"n": int(t0 * 5), "complete": t0 * 1000, "assemblyMs": 0.0, "srcFps": 5.0}
            else:
                try:
                    await asyncio.wait_for(video.new_frame.wait(), timeout=2.0)
                except TimeoutError:
                    continue
                if video.frames == last or video.frame is None:
                    continue
                last = video.frames
                meta = {"n": video.frame_number, "complete": video.last_frame_at * 1000,
                        "assemblyMs": round(video.assembly_ms, 2), "srcFps": round(video.source_fps, 1)}
                frame, height = video.frame, video.height
                t0 = time.time()
                jpeg = await asyncio.to_thread(frame_to_jpeg, frame, height)
            meta["encodeMs"] = round((time.time() - t0) * 1000, 2)
            meta["sent"] = time.time() * 1000
            head = json.dumps(meta).encode()
            await ws.send_bytes(struct.pack("<I", len(head)) + head + jpeg)
            last_sent = await _pace(last_sent, fps)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await reader
        await c.device.leave_stream("video")


@router.websocket("/ws/telnet")
async def telnet_ws(ws: WebSocket):
    """Browser terminal ↔ the Ultimate's Telnet remote menu. Only relays what the user types."""
    c: Container = ws.app.state.container
    await ws.accept()
    s = c.settings
    crlf = "\r\n"
    if s.SIMULATE_C64 or not s.C64_ULTIMATE_HOST:
        await ws.send_text(f"{crlf}  The remote menu needs a real C64 Ultimate (not available in simulation mode).{crlf}")
        await ws.close()
        return
    try:
        reader, writer = await open_telnet(s.C64_ULTIMATE_HOST.split(":")[0], 23)
    except (OSError, TimeoutError) as exc:
        await ws.send_text(f"{crlf}  Could not open Telnet on the Ultimate: {exc}{crlf}"
                           f"  Enable Telnet in the Ultimate network settings.{crlf}")
        await ws.close()
        return
    c.device.caps.record_use("telnet", True)
    log.info("telnet remote menu opened")
    filt = TelnetFilter()

    async def device_to_browser():
        while True:
            data = await reader.read(4096)
            if not data:
                break
            text = filt.feed(data).decode("latin-1")
            if text:
                await ws.send_text(text)

    async def browser_to_device():
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            data = msg.get("bytes") or (msg.get("text") or "").encode("latin-1", errors="ignore")
            if data:
                writer.write(escape_outgoing(data))
                await writer.drain()

    tasks = [asyncio.create_task(device_to_browser()), asyncio.create_task(browser_to_device())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except (WebSocketDisconnect, RuntimeError, OSError):
        pass
    finally:
        for t in tasks:
            t.cancel()
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        with contextlib.suppress(Exception):
            await ws.close()
        log.info("telnet remote menu closed")


@router.websocket("/ws/audio")
async def audio_ws(ws: WebSocket):
    c: Container = ws.app.state.container
    await ws.accept()
    if c.device.simulator is None:
        try:
            await c.device.join_stream("audio")
        except (UltimateError, ValueError) as exc:
            await ws.send_json({"type": "error", "detail": str(exc)})
            await ws.close()
            return
    await ws.send_json({"sampleRate": AUDIO_RATE_PAL, "channels": 2, "format": "s16le"})
    q = c.device.streams.audio.subscribe()
    try:
        while True:
            pcm = await q.get()
            await ws.send_bytes(pcm)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        c.device.streams.audio.unsubscribe(q)
        if c.device.simulator is None:
            await c.device.leave_stream("audio")


@router.websocket("/ws/joystick")
async def joystick_ws(ws: WebSocket):
    """Live joystick for gamepad / keyboard passthrough. The client sends whole states,
    {"port": 2, "inputs": ["up", "fire"]}, on every change; [] releases. Lower latency than one
    HTTP request per press, and everything this socket held is released when it closes."""
    from app.ultimate.input import InputUnsupported

    c: Container = ws.app.state.container
    await ws.accept()
    touched: set[int] = set()
    last_error = ""
    async with c.audit.action("ui", "input.joystick.live", "gamepad/keyboard passthrough started"):
        pass
    try:
        while True:
            msg = await ws.receive_json()
            if not isinstance(msg, dict):
                continue
            port = msg.get("port") or c.device.inputs.joystick_port
            inputs = msg.get("inputs") or []
            if not isinstance(inputs, list) or port not in (1, 2):
                continue
            try:
                await c.device.inputs.set_joystick([str(i) for i in inputs][:5], port)
                touched.add(port)
                last_error = ""
            except (InputUnsupported, ValueError, UltimateError) as exc:
                if str(exc) != last_error:  # report each distinct problem once
                    last_error = str(exc)
                    await ws.send_json({"type": "error", "detail": last_error})
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        for port in touched:
            with contextlib.suppress(Exception):
                await c.device.inputs.set_joystick([], port)


@router.websocket("/ws")
async def events_ws(ws: WebSocket):
    """Live events: status, audit, launch, session, scan, vision. Clients may send
    {"type": "release_all"} — and inputs held by the UI are released when it disconnects."""
    c: Container = ws.app.state.container
    await ws.accept()
    q = c.hub.subscribe()
    await ws.send_json({"type": "status", "data": c.device.status()})
    await ws.send_json({"type": "audit_backlog", "data": list(c.audit.recent)[:30]})

    async def reader():
        while True:
            msg = await ws.receive_json()
            if isinstance(msg, dict) and msg.get("type") == "release_all":
                await c.device.release_all_inputs("ui websocket")
            elif isinstance(msg, dict) and msg.get("type") == "ping":
                await ws.send_json({"type": "pong"})

    reader_task = asyncio.create_task(reader())
    try:
        while True:
            get_task = asyncio.create_task(q.get())
            done, _ = await asyncio.wait({get_task, reader_task}, return_when=asyncio.FIRST_COMPLETED)
            if reader_task in done:
                get_task.cancel()
                break
            await ws.send_json(get_task.result())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        reader_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await reader_task
        c.hub.unsubscribe(q)
        # A closed controller must never leave a joystick direction or key held down.
        if c.device.inputs.held:
            await c.device.release_all_inputs("ui disconnected")
