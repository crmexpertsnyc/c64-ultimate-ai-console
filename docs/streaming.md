# Video & audio streaming

Documented for Ultimate 64‑class hardware (the C64 Ultimate is U64‑based). The console probes
`PUT /v1/streams/{video,audio}:stop` and only offers streaming when the route exists.

## What the Ultimate sends (UDP, not browser‑playable)

| Stream | Port | Datagram |
|---|---|---|
| video | 11000 | 12‑byte LE header: seq, frame, line (bit 15 = last packet of frame), pixels/line = 384, lines/packet = 4, bpp = 4, encoding = 0 — then 768 bytes: 4 lines × 384 px of 4‑bit VIC colours, left pixel in the low nibble. 68 packets per PAL frame (384×272; NTSC 384×240). |
| audio | 11001 | 2‑byte seq + 192 stereo samples s16le (L,R), ≈ 47 983 Hz PAL / 47 940 Hz NTSC |

## Adapter (`backend/app/ultimate/streams.py`)

* Binds UDP sockets inside the backend event loop (non‑blocking; if a port can't be bound the stream is
  reported unavailable and nothing else is affected).
* `PUT /v1/streams/<x>:start?ip=<this-machine>:<port>` tells the Ultimate where to send.
* Video: packets are reassembled into frames (fast nibble unpack via `bytes.translate`), converted to JPEG
  with the Pepto palette, and served as MJPEG (`/api/streams/video.mjpeg`) — an `<img>` plays it.
* Audio: PCM chunks are fanned out over `WS /ws/audio`; the browser schedules them with WebAudio at the
  device sample rate (drops rather than buffers when a client falls behind).
* Streams are stopped on shutdown.

## Display page transport and latency readout

The Display page receives frames over `WS /ws/video` (the MJPEG URL remains as a fallback). Each message
is a 4‑byte length + JSON metadata (`complete` = time the last UDP packet of the frame arrived,
`assemblyMs`, `encodeMs`, `sent`, `srcFps`) + the JPEG. Only the newest frame is sent or decoded; stale
frames are skipped, never queued. The browser aligns its clock with the server via ping/pong and shows
(**⏱ Latency**): delay from frame complete to drawn (avg / p95), source and displayed fps, and the
breakdown (frame transfer, encode, delivery, decode + draw).

Measured on a C64 Ultimate 1.1.0s2 over wired LAN, console and browser on the same PC (2026-09-26):
50.1 fps from the C64, 49.6 fps delivered, frame complete → received by the page **4.0 ms avg / 5.0 ms p95**
(before browser decode/draw). The C64 itself needs ~17 ms to send each frame (first to last packet).
Not measurable in software: the Ultimate's internal capture and the monitor's refresh.

## Networking

* The target IP is auto‑detected (the local address used to reach the Ultimate). Override with
  `STREAM_TARGET_HOST`.
* Docker bridge networking: publish `11000/udp` and `11001/udp` (compose does) and set
  `STREAM_TARGET_HOST` to the Docker host's LAN IP. On Linux, `network_mode: host` is simplest.
* Firewalls must allow inbound UDP 11000–11001 from the Ultimate.

## Limits / next steps

* MJPEG is capped at 25 fps by default (`?fps=` on the MJPEG URL, max 50).
* A WebCodecs/WebRTC path would reduce latency; heavier native encoding would run as a separate service
  consuming the same UDP packets — the adapter boundary is `StreamService`.
* Simulation mode renders the simulated text screen as a picture and has no audio.

## Go live, recording and OBS

* **Viewer counting:** the Display page, the OBS stream view, a live broadcast/recording and screenshot
  capture each "join" the stream; the Ultimate's stream starts for the first and stops 5 s after the last.
* **Go live / Record** (`POST /api/live/{live|record}/start`, `/api/live/stop`): one ffmpeg process gets raw
  RGB frames at a constant 50 fps (latest frame, repeated if none arrived) plus the PCM audio relayed over
  local UDP, padded with silence so audio never lags real time (starved audio would stall ffmpeg's
  interleaving). Input probing is disabled (`-probesize 32 -analyzeduration 0`) — otherwise ffmpeg analyses
  ~5 s of live input before starting. Output: H.264 zerolatency + AAC, picture doubled with sharp pixels then
  scaled to 720p height, 1280×720. RTMP to Twitch/YouTube/custom, or MP4 in `DATA_DIR/recordings`.
  ffmpeg comes from `FFMPEG_PATH`, the PATH, or the bundled `imageio-ffmpeg` package.
  Verified on a real C64 Ultimate: 50 fps at 0.996× real-time speed, clean stop (moov written) in 0.3 s.
* **OBS:** Browser Source → `http://<host>:8064/stream-view` (1280×720, "Control audio via OBS").
  `?audio=0` mutes, `?bg=transparent` for overlays.
* **Screenshots** are PNGs rendered from the raw VIC frame (lossless, 768×544) in `DATA_DIR/screenshots`.
