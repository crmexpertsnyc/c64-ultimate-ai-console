import json
import struct
import time

import pytest
from helpers import make_d64

from app.ultimate.streams import VIDEO_HEADER, VideoAssembler, unpack_pixels


def _make_library(tmp_path):
    lib = tmp_path / "C64"
    lib.mkdir()
    (lib / "Summer Games Disk 1.d64").write_bytes(make_d64("SG1"))
    (lib / "Summer Games Disk 2.d64").write_bytes(make_d64("SG2"))
    (lib / "Bruce Lee.prg").write_bytes(b"\x01\x08" + b"\xea" * 30)
    (lib / "Boulder Dash.d64").write_bytes(make_d64("BD", [("BOULDER", b"\x01\x08XYZ")]))
    return lib


def _scan(c, lib):
    r = c.post("/api/library/scan", json={"paths": [str(lib)]})
    assert r.status_code == 200, r.text
    for _ in range(100):
        st = c.get("/api/library/scan").json()
        if not st["running"]:
            return st
        time.sleep(0.05)
    raise AssertionError("scan did not finish")


def _wait_job(c, job_id):
    for _ in range(200):
        job = c.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_openapi_and_health(app_client):
    with app_client() as c:
        spec = c.get("/openapi.json").json()
        for path in ("/api/device", "/api/capabilities", "/api/library", "/api/games/{game_id}",
                     "/api/games/{game_id}/play", "/api/device/reset", "/api/device/menu", "/api/device/input",
                     "/api/device/type", "/api/device/joystick", "/api/session/disk/{number}", "/api/command",
                     "/api/current-session"):
            assert path in spec["paths"], path
        assert c.get("/api/health").json()["ok"]


@pytest.mark.parametrize("profile", ["modern", "legacy"])
def test_play_prg_and_multidisk_session(app_client, tmp_path, profile):
    lib = _make_library(tmp_path)
    with app_client(SIMULATE_PROFILE=profile) as c:
        sim = c.app.state.container.device.simulator
        st = _scan(c, lib)
        assert st["results"][0]["games_created"] == 3

        # "Play Bruce Lee" → PRG via run_prg upload
        r = c.post("/api/command", json={"text": "Play Bruce Lee"}).json()
        assert r["ok"], r
        job = _wait_job(c, r["data"]["job"]["id"])
        assert job["status"] == "done", job
        assert sim.running == "uploaded program"

        # Disk game: mount → reset → READY → LOAD"*",8,1 → RUN
        r = c.post("/api/command", json={"text": "Load Summer Games"}).json()
        assert r["ok"], r
        job = _wait_job(c, r["data"]["job"]["id"])
        assert job["status"] == "done", job
        names = [s["name"] for s in job["steps"]]
        assert any("mount" in n for n in names) and any("reset" in n for n in names)
        assert any('LOAD"*",8,1' in n for n in names)
        for _ in range(40):  # legacy mode queues RUN in the keyboard buffer; let simulated time pass
            if sim.running:
                break
            c.get("/api/device/screen")
            time.sleep(0.03)
        assert sim.running is not None  # program started by RUN
        assert sim.drives["a"]["image_file"] == "uploaded.d64"

        session = c.get("/api/current-session").json()["session"]
        assert session["title"] == "Summer Games" and session["diskCount"] == 2 and session["currentDisk"] == 1

        r = c.post("/api/command", json={"text": "Put disk 2 in"}).json()
        assert r["ok"], r
        assert c.get("/api/current-session").json()["session"]["currentDisk"] == 2
        r = c.post("/api/command", json={"text": "next disk"}).json()
        assert not r["ok"] and "last disk" in r["message"]

        games = c.get("/api/library", params={"recent": True}).json()
        assert {g["title"] for g in games["items"]} == {"Bruce Lee", "Summer Games"}


def test_joystick_on_legacy_is_reported_unsupported(app_client):
    with app_client(SIMULATE_PROFILE="legacy") as c:
        r = c.post("/api/device/joystick", json={"inputs": ["fire"], "port": 2})
        assert r.status_code == 409 and r.json()["kind"] == "unsupported"
        r = c.post("/api/command", json={"text": "press fire"}).json()
        assert not r["ok"] and "joystick" in r["message"].lower()


def test_power_off_requires_confirmation(app_client):
    with app_client() as c:
        sim = c.app.state.container.device.simulator
        assert c.post("/api/device/power-off", json={}).status_code == 400
        assert sim.powered
        r = c.post("/api/command", json={"text": "power off"}).json()
        assert r["needsConfirmation"] and sim.powered
        r = c.post("/api/command", json={"text": "power off", "confirm": True}).json()
        assert r["ok"] and not sim.powered


def test_secrets_never_exposed(app_client, tmp_path):
    with app_client() as c:
        c.put("/api/settings", json={"C64_ULTIMATE_PASSWORD": "pw-XYZ-123", "AI_API_KEY": "sk-secret-999"})
        s = c.get("/api/settings").json()
        assert s["C64_ULTIMATE_PASSWORD"] == "" and s["C64_ULTIMATE_PASSWORD_SET"] is True
        assert s["AI_API_KEY"] == "" and s["AI_API_KEY_SET"] is True
        c.post("/api/command", json={"text": "type pw-XYZ-123"})
        dump = json.dumps(c.get("/api/audit").json()) + json.dumps(c.get("/api/troubleshooting").json())
        assert "pw-XYZ-123" not in dump and "sk-secret-999" not in dump
        # Empty secret in an update keeps the stored value.
        c.put("/api/settings", json={"C64_ULTIMATE_PASSWORD": ""})
        assert c.get("/api/settings").json()["C64_ULTIMATE_PASSWORD_SET"] is True


def test_audit_records_api_calls(app_client):
    with app_client() as c:
        c.post("/api/command", json={"text": "reset the c64"})
        entry = next(e for e in c.get("/api/audit").json() if e["operation"] == "command.RESET")
        assert entry["success"] and entry["userCommand"] == "reset the c64"
        assert entry["intent"]["intent"] == "RESET"
        assert any(call["path"] == "/v1/machine:reset" for call in entry["apiCalls"])


def test_menu_endpoints(app_client):
    with app_client() as c:
        assert c.get("/api/device/menu").json()["open"] is False
        r = c.post("/api/device/menu", json={"action": "open"}).json()
        assert r["verified"]
        screen = c.get("/api/device/menu").json()["screen"]
        assert len(screen["rows"]) == 25 and len(screen["rows"][0]["cells"]) == 40
        r = c.post("/api/device/menu", json={"action": "down"}).json()
        assert r["changed"]


def test_shutdown_releases_inputs(app_client):
    with app_client() as c:
        sim = c.app.state.container.device.simulator
        c.post("/api/device/joystick", json={"inputs": ["right"], "transition": "press"})
        assert sim.held_joy[2] == {"right"}
    assert sim.held_joy[2] == set()


def test_video_assembler():
    asm = VideoAssembler()
    payload = bytes([0x21]) * (384 * 4 // 2)
    for i, line in enumerate(range(0, 272, 4)):
        flag = 0x8000 if line == 268 else 0
        pkt = VIDEO_HEADER.pack(i, 7, line | flag, 384, 4, 4, 0) + payload
        done = asm.feed(pkt)
    assert done and asm.frames == 1 and asm.height == 272
    assert asm.frame[:2] == bytes([1, 2])  # low nibble is the left pixel
    assert unpack_pixels(b"\xf0") == b"\x00\x0f"
    assert asm.feed(struct.pack("<H", 1)) is False and asm.bad_packets == 1


def test_video_websocket_sends_timed_frames(app_client):
    import json
    import struct
    with app_client() as c, c.websocket_connect("/ws/video?fps=10") as ws:
        data = ws.receive_bytes()
        n = struct.unpack_from("<I", data)[0]
        meta = json.loads(data[4:4 + n])
        assert data[4 + n:4 + n + 2] == b"\xff\xd8"  # JPEG
        assert {"complete", "sent", "encodeMs", "assemblyMs", "srcFps"} <= set(meta)
        assert meta["sent"] >= meta["complete"]
        ws.send_json({"type": "ping", "t": 123})
        for _ in range(10):
            msg = ws.receive()
            if msg.get("text"):
                pong = json.loads(msg["text"])
                assert pong["type"] == "pong" and pong["t"] == 123 and pong["server"] > 0
                break
        else:
            raise AssertionError("no pong")


def test_disk_launch_types_boot_program_name(app_client, tmp_path):
    lib = tmp_path / "demo"
    lib.mkdir()
    (lib / "Megademo.d64").write_bytes(make_d64("MEGA", [("DIR ART", b"\x00\x40\x00"), ("MEGADEMO", b"\x01\x08\xea")]))
    with app_client() as c:
        _scan(c, lib)
        game = c.get("/api/library").json()["items"][0]
        job = c.post(f"/api/games/{game['id']}/play", json={"wait": True}).json()
        job = _wait_job(c, job["id"])
        assert any('LOAD"MEGADEMO",8,1' in s["name"] for s in job["steps"]), job["steps"]


def test_switching_to_browser_resets_the_c64(app_client, tmp_path):
    lib = tmp_path / "demo"
    lib.mkdir()
    (lib / "Megademo.d64").write_bytes(make_d64("MEGA", [("MEGADEMO", b"\x01\x08\xea")]))
    with app_client() as c:
        # Nothing playing on the C64: nothing to reset.
        assert c.post("/api/device/handoff-to-browser").json() == {"reset": False}
        _scan(c, lib)
        game = c.get("/api/library").json()["items"][0]
        _wait_job(c, c.post(f"/api/games/{game['id']}/play", json={"wait": True}).json()["id"])
        assert c.get("/api/current-session").json()["session"]["gameId"] == game["id"]
        r = c.post("/api/device/handoff-to-browser").json()
        assert r == {"reset": True, "title": game["title"]}
        assert c.get("/api/current-session").json()["session"]["gameId"] is None
        audit = c.get("/api/audit", params={"limit": 5}).json()
        assert any(e["operation"] == "machine.reset" for e in (audit["items"] if isinstance(audit, dict) else audit))
        assert c.post("/api/device/handoff-to-browser").json() == {"reset": False}  # already stopped
