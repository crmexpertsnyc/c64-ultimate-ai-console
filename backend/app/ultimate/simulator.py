"""In-process simulated C64 Ultimate (SIMULATE_C64=true).

It implements the documented REST routes with realistic behaviour so every layer
above the transport can be exercised without hardware:

* 64 KB of RAM with KERNAL-like zero page values ($0314 IRQ vector, $C6, $0289, $0288)
* a tiny BASIC screen editor that consumes the keyboard buffer and REST key taps and
  understands LOAD / RUN / SYS, printing READY. like the real machine
* drives A/B with mount/remove/on/off/reset/set_mode
* runners (PRG/CRT/SID/MOD) that mark a program as running and replace the IRQ vector
* an Ultimate-style menu (menu_button, menu_screen, keyboard navigation)
* streams start/stop bookkeeping, configs, files:info

Profiles: ``modern`` (machine:input + menu_screen present) and ``legacy`` (routes absent,
matching Commodore firmware 1.1.0 / Spiffy).
"""

from __future__ import annotations

import os
import re
import time
from typing import Any

from .petscii import JOYSTICK_INPUTS, REST_KEYS, char_to_rest_keys, char_to_screen_code

JSON = "application/json"
BIN = "application/octet-stream"

BOOT_LINES = [
    "",
    "    **** COMMODORE 64 BASIC V2 ****",
    "",
    " 64K RAM SYSTEM  38911 BASIC BYTES FREE",
    "",
    "READY.",
]

MENU_ROOT = ["Usb0", "Flash", "Temp", "SD", "Assembly64", "Configuration"]
MENU_DIRS = {
    "Usb0": ["..", "Games", "Music", "Demos", "Tools"],
    "Games": ["..", "Bruce Lee.d64", "Impossible Mission.d64", "Summer Games Disk 1.d64",
              "Summer Games Disk 2.d64", "Boulder Dash.prg"],
}

# Reverse lookup: REST key combination → character typed.
_COMBO_TO_CHAR: dict[frozenset[str], str] = {}
for _o in range(0x20, 0x7F):
    _ch = chr(_o)
    _combo = char_to_rest_keys(_ch)
    if _combo and not ("a" <= _ch <= "z"):
        _COMBO_TO_CHAR.setdefault(frozenset(_combo), _ch)
_COMBO_TO_CHAR[frozenset({"return"})] = "\r"


def _err(status: int, msg: str) -> tuple[int, dict, str]:
    return status, {"errors": [msg]}, JSON


def _ok(extra: dict | None = None) -> tuple[int, dict, str]:
    return 200, {**(extra or {}), "errors": []}, JSON


class SimulatedUltimate:
    def __init__(self, profile: str = "modern", load_delay: float = 0.4):
        self.profile = profile
        self.load_delay = load_delay
        self.calls: list[tuple[str, str]] = []
        self.reset_count = 0
        self.powered = True
        self.streams: dict[str, str | None] = {"video": None, "audio": None, "debug": None}
        self.drives: dict[str, dict[str, Any]] = {
            "a": {"enabled": True, "bus_id": 8, "type": "1541", "rom": "1541.rom", "image_file": "",
                  "image_path": ""},
            "b": {"enabled": False, "bus_id": 9, "type": "1541", "rom": "1541.rom", "image_file": "",
                  "image_path": ""},
            "IEC Drive": {"enabled": False, "bus_id": 11, "type": "DOS emulation", "partitions":
                          [{"id": 0, "path": "/Temp/"}]},
        }
        self.config: dict[str, dict[str, Any]] = {
            "Drive A Settings": {"Drive": "Enabled", "Drive Type": "1541", "Drive Bus ID": 8},
            "U64 Specific Settings": {"System Mode": "PAL", "Joystick Swapper": "Normal"},
            "Audio Mixer": {"Vol UltiSid 1": "0 dB", "Vol UltiSid 2": "0 dB"},
        }
        self._boot()

    # ------------------------------------------------------------ machine state
    def _boot(self) -> None:
        self.mem = bytearray(65536)
        self.mem[0x0314:0x0316] = bytes([0x31, 0xEA])
        self.mem[0x0289] = 10
        self.mem[0x0288] = 0x04
        self.mem[0x00C6] = 0
        self.lines = [""] * 25
        for i, line in enumerate(BOOT_LINES):
            self.lines[i] = line
        self.cursor_row = len(BOOT_LINES)
        self.input_line = ""
        self.loaded: str | None = None
        self.running: str | None = None
        self.paused = False
        self.menu_open = False
        self.menu_path: list[str] = []
        self.menu_sel = 0
        self.held_keys: set[str] = set()
        self.held_joy: dict[int, set[str]] = {1: set(), 2: set()}
        self.typed_log: list[str] = []
        self._pending_ready_at: float | None = None
        self._render()

    def _render(self) -> None:
        for r, line in enumerate(self.lines):
            text = line.ljust(40)[:40]
            base = 0x0400 + r * 40
            self.mem[base:base + 40] = bytes(char_to_screen_code(c) for c in text)

    def _print(self, text: str) -> None:
        if self.cursor_row >= 25:
            self.lines = self.lines[1:] + [""]
            self.cursor_row = 24
        self.lines[self.cursor_row] = text[:40]
        self.cursor_row += 1

    def _start_program(self, name: str) -> None:
        self.running = name
        self.mem[0x0314:0x0316] = bytes([0x00, 0xC0])  # program installed its own IRQ handler
        self.lines = [""] * 25
        self.lines[10] = f"  {name.upper()[:36]}"
        self.lines[12] = "  (SIMULATED PROGRAM RUNNING)"
        self.cursor_row = 25
        self._render()

    def _tick(self) -> None:
        """Advance simulated time: finish loads, drain the keyboard buffer."""
        if self._pending_ready_at and time.monotonic() >= self._pending_ready_at:
            self._pending_ready_at = None
            self._print("READY.")
            self._render()
        if self.running or self.paused or self._pending_ready_at:
            return
        n = min(self.mem[0x00C6], 10)
        if n:
            data = bytes(self.mem[0x0277:0x0277 + n])
            self.mem[0x00C6] = 0
            for code in data:
                self._editor_char(chr(code) if code != 0x0D else "\r")

    def _editor_char(self, ch: str) -> None:
        if self.running or self._pending_ready_at:
            return
        self.typed_log.append(ch)
        if ch == "\r":
            line = self.input_line
            self.input_line = ""
            self._execute(line.strip())
        elif ch == "\x14":
            self.input_line = self.input_line[:-1]
        else:
            self.input_line += ch.upper()
            row = min(self.cursor_row, 24)
            self.lines[row] = self.input_line[:40]
        self._render()

    def _execute(self, line: str) -> None:
        if self.cursor_row < 25:
            self.lines[self.cursor_row] = line[:40]
        self.cursor_row += 1
        if not line:
            return
        m = re.match(r'^LOAD\s*"([^"]*)"\s*,\s*(\d+)(?:\s*,\s*(\d+))?$', line)
        if m:
            name, dev = m.group(1), int(m.group(2))
            drive = next((d for d in self.drives.values() if d.get("bus_id") == dev and d.get("enabled")), None)
            if not drive:
                self._print("?DEVICE NOT PRESENT  ERROR")
                self._print("READY.")
                return
            if not drive.get("image_file"):
                self._print("")
                self._print(f"SEARCHING FOR {name}")
                self._print("?FILE NOT FOUND  ERROR")
                self._print("READY.")
                return
            self._print("")
            self._print(f"SEARCHING FOR {name}")
            self._print("LOADING")
            self.loaded = os.path.splitext(os.path.basename(drive["image_file"]))[0]
            self._pending_ready_at = time.monotonic() + self.load_delay
            return
        if line == "RUN":
            if self.loaded:
                self._start_program(self.loaded)
            else:
                self._print("")
                self._print("READY.")
            return
        m = re.match(r"^SYS\s*(\d+)$", line)
        if m:
            self._start_program(f"SYS {m.group(1)}")
            return
        self._print("?SYNTAX  ERROR")
        self._print("READY.")

    # ------------------------------------------------------------------- menu
    def _menu_items(self) -> list[str]:
        if not self.menu_path:
            return MENU_ROOT
        return MENU_DIRS.get(self.menu_path[-1], ["..", "(empty)"])

    def _menu_screen(self) -> bytes:
        chars = bytearray(b" " * 1000)
        colors = bytearray([(6 << 4) | 14] * 1000)  # light blue on blue

        def put(row: int, col: int, text: str, reverse: bool = False, color: int | None = None) -> None:
            for i, ch in enumerate(text[: 40 - col]):
                idx = row * 40 + col + i
                chars[idx] = (ord(ch) & 0x7F) | (0x80 if reverse else 0)
                if color is not None:
                    colors[idx] = color

        put(0, 0, " *** C64 Ultimate (simulated) *** ".center(40), reverse=True, color=(6 << 4) | 1)
        put(1, 1, "/" + "/".join(self.menu_path))
        items = self._menu_items()
        for i, item in enumerate(items[:20]):
            put(3 + i, 2, item.ljust(24), reverse=(i == self.menu_sel))
        put(24, 0, "F1/F7 Page  RUN/STOP Exit  RETURN Select".ljust(40), color=(6 << 4) | 15)
        return bytes(chars + colors)

    def _menu_key(self, keys: set[str]) -> None:
        items = self._menu_items()
        shift = bool(keys & {"left_shift", "right_shift"})
        if "cursor_up_down" in keys:
            self.menu_sel = max(0, self.menu_sel - 1) if shift else min(len(items) - 1, self.menu_sel + 1)
        elif "cursor_left_right" in keys:
            if shift and self.menu_path:
                self.menu_path.pop()
                self.menu_sel = 0
        elif "return" in keys:
            item = items[self.menu_sel]
            if item == "..":
                if self.menu_path:
                    self.menu_path.pop()
            elif item in MENU_DIRS or item in MENU_ROOT:
                self.menu_path.append(item)
            self.menu_sel = 0
        elif "run_stop" in keys:
            self.menu_open = False
        elif "clr_home" in keys:
            self.menu_sel = 0
        elif "f1" in keys:
            self.menu_sel = max(0, self.menu_sel - 10)
        elif "f7" in keys:
            self.menu_sel = min(len(items) - 1, self.menu_sel + 10)

    # ------------------------------------------------------------------ input
    def _validate_events(self, body: Any) -> str | None:
        if not isinstance(body, dict) or not isinstance(body.get("events"), list):
            return "body must be an object with an 'events' array"
        events = body["events"]
        if not 1 <= len(events) <= 64:
            return "events must contain 1..64 entries"
        for ev in events:
            kind = ev.get("kind")
            if kind == "release_all":
                continue
            inputs = ev.get("inputs") or []
            if ev.get("transition") not in ("tap", "press", "release"):
                return "invalid transition"
            if len(set(inputs)) != len(inputs) or not inputs:
                return "inputs must be unique and non-empty"
            if kind == "keyboard":
                if len(inputs) > 8 or any(i not in REST_KEYS for i in inputs):
                    return f"invalid keyboard inputs {inputs}"
                if "restore" in inputs and (len(inputs) > 1 or ev["transition"] != "tap"):
                    return "restore must be alone with transition tap"
            elif kind == "joystick":
                if ev.get("port") not in (1, 2) or any(i not in JOYSTICK_INPUTS for i in inputs):
                    return "invalid joystick event"
            else:
                return f"unknown kind {kind}"
        return None

    def _apply_events(self, events: list[dict[str, Any]]) -> None:
        for ev in events:
            kind = ev["kind"]
            if kind == "release_all":
                self.held_keys.clear()
                self.held_joy = {1: set(), 2: set()}
                continue
            inputs, transition = set(ev["inputs"]), ev["transition"]
            if kind == "joystick":
                held = self.held_joy[ev["port"]]
                if transition == "press":
                    held |= inputs
                elif transition == "release":
                    held -= inputs
                continue
            if transition == "press":
                self.held_keys |= inputs
                continue
            if transition == "release":
                self.held_keys -= inputs
                continue
            # tap
            if self.menu_open:
                self._menu_key(inputs)
                continue
            if "run_stop" in inputs and self.running:
                continue
            if len(inputs) == 1 and next(iter(inputs)) in "abcdefghijklmnopqrstuvwxyz0123456789":
                self._editor_char(next(iter(inputs)).upper())
            elif inputs == {"space"}:
                self._editor_char(" ")
            elif inputs == {"inst_del"}:
                self._editor_char("\x14")
            else:
                ch = _COMBO_TO_CHAR.get(frozenset(inputs))
                if ch:
                    self._editor_char(ch)

    # ------------------------------------------------------------------ routing
    async def handle(self, method: str, path: str, params: dict[str, Any], body: bytes | None,
                     json_body: Any) -> tuple[int, Any, str]:
        status, content, ctype = self._handle(method, path, params, body, json_body)
        if self.profile == "legacy" and status == 404 and content == {"errors": ["Unknown route"]}:
            # Observed on a real C64 Ultimate 1.1.0s2: unknown routes → bare 404, empty body.
            return 404, b"", "text/plain"
        return status, content, ctype

    def _handle(self, method: str, path: str, params: dict[str, Any], body: bytes | None,
                json_body: Any) -> tuple[int, Any, str]:
        self.calls.append((method, path))
        self._tick()
        if not self.powered:
            return 503, {"errors": ["powered off (simulated)"]}, JSON
        route = path.split("?")[0]

        if method == "GET" and route == "/v1/version":
            return _ok({"version": "0.1"})
        if method == "GET" and route == "/v1/info":
            fw = "1.2.0" if self.profile == "modern" else "1.1.0s2"
            return _ok({"product": "C64 Ultimate", "firmware_version": fw, "fpga_version": "121",
                        "core_version": "1.46", "hostname": "c64u-sim", "unique_id": "SIM00C64"})

        if route.startswith("/v1/machine:"):
            return self._machine(method, route.split(":", 1)[1], params, body, json_body)
        if route == "/v1/drives" and method == "GET":
            return _ok({"drives": [{k: v} for k, v in self.drives.items()]})
        if route.startswith("/v1/drives/"):
            return self._drive(method, route[len("/v1/drives/"):], params, body)
        if route.startswith("/v1/runners:"):
            return self._runner(method, route.split(":", 1)[1], params, body)
        if route.startswith("/v1/streams/"):
            m = re.match(r"^/v1/streams/(\w+):(start|stop)$", route)
            if not m or m.group(1) not in self.streams or method != "PUT":
                return _err(404, "Unknown route")
            if m.group(2) == "start":
                if not params.get("ip"):
                    return _err(400, "Missing parameter 'ip'")
                self.streams[m.group(1)] = str(params["ip"])
            else:
                self.streams[m.group(1)] = None
            return _ok()
        if route == "/v1/configs" and method == "GET":
            return _ok({"categories": list(self.config)})
        if route.startswith("/v1/configs/"):
            parts = [p for p in route[len("/v1/configs/"):].split("/") if p]
            from urllib.parse import unquote
            parts = [unquote(p) for p in parts]
            cat = self.config.get(parts[0])
            if cat is None:
                return _err(404, "Category not found")
            if len(parts) == 1 and method == "GET":
                return _ok({parts[0]: cat})
            if len(parts) == 2:
                if parts[1] not in cat:
                    return _err(404, "Item not found")
                if method == "GET":
                    return _ok({parts[0]: {parts[1]: {"current": cat[parts[1]]}}})
                if method == "PUT":
                    if "value" not in params:
                        return _err(400, "Missing parameter 'value'")
                    cat[parts[1]] = params["value"]
                    return _ok()
            return _err(404, "Unknown route")
        if route.startswith("/v1/files/") and route.endswith(":info") and method == "GET":
            target = route[len("/v1/files/"):-len(":info")]
            if target.strip("/") in ("Temp", "Usb0", "Flash"):
                return _ok({"files": {"path": "/" + target.strip("/"), "size": 0, "extension": ""}})
            return _err(404, "File not found")
        return _err(404, "Unknown route")

    def _machine(self, method: str, action: str, params: dict[str, Any], body: bytes | None,
                 json_body: Any) -> tuple[int, Any, str]:
        simple = {"reset", "reboot", "pause", "resume", "poweroff", "menu_button"}
        if action in simple:
            if method != "PUT":
                return _err(404, "Unknown route")
            if action in ("reset", "reboot"):
                self.reset_count += 1
                streams = dict(self.streams)
                self._boot()
                self.streams = streams
            elif action == "pause":
                self.paused = True
            elif action == "resume":
                self.paused = False
            elif action == "poweroff":
                self.powered = False
            elif action == "menu_button":
                self.menu_open = not self.menu_open
                self.menu_path, self.menu_sel = [], 0
            return _ok()
        if action == "menu_screen" and method == "GET":
            if self.profile != "modern":
                return _err(404, "Unknown route")
            if not self.menu_open:
                return _err(404, "No readable menu active")
            return 200, self._menu_screen(), BIN
        if action == "input":
            if self.profile != "modern":
                return _err(404, "Unknown route")
            if method == "GET":
                return _ok({"keyboard": {"inputs": sorted(self.held_keys)},
                            "joysticks": [{"port": p, "inputs": sorted(v)} for p, v in self.held_joy.items()]})
            if method == "POST":
                problem = self._validate_events(json_body)
                if problem:
                    return _err(400, problem)
                self._apply_events(json_body["events"])
                return _ok()
        if action == "readmem" and method == "GET":
            try:
                addr = int(str(params["address"]), 16)
                length = int(params.get("length", 256))
            except (KeyError, ValueError):
                return _err(400, "Missing or invalid parameter 'address'")
            if not 1 <= length <= 65536:
                return _err(400, "Invalid length")
            data = bytes(self.mem[(addr + i) & 0xFFFF] for i in range(length))
            return 200, data, BIN
        if action == "writemem":
            try:
                addr = int(str(params["address"]), 16)
            except (KeyError, ValueError):
                return _err(400, "Missing or invalid parameter 'address'")
            if method == "PUT":
                if "data" not in params:
                    return _err(400, "Missing parameter 'data'")
                data = bytes.fromhex(str(params["data"]))
                if len(data) > 128:
                    return _err(400, "Too much data")
            elif method == "POST":
                data = body or b""
            else:
                return _err(404, "Unknown route")
            self.mem[addr:addr + len(data)] = data
            return _ok()
        if action == "debugreg" and method == "GET":
            return _ok({"value": "00"})
        return _err(404, "Unknown route")

    def _drive(self, method: str, spec: str, params: dict[str, Any], body: bytes | None) -> tuple[int, Any, str]:
        if ":" not in spec or method not in ("PUT", "POST"):
            return _err(404, "Unknown route")
        name, action = spec.split(":", 1)
        drive = self.drives.get(name)
        if drive is None:
            return _err(404, f"Drive '{name}' not found")
        if action == "mount":
            if method == "PUT":
                image = params.get("image")
                if not image:
                    return _err(400, "Missing parameter 'image'")
                if self.profile == "legacy":  # real 1.1.0s2: full path in image_file, empty image_path
                    drive["image_file"], drive["image_path"] = str(image), ""
                else:
                    drive["image_file"] = os.path.basename(str(image))
                    drive["image_path"] = os.path.dirname(str(image)) or "/"
            else:
                if not body:
                    return _err(400, "No attachment")
                drive["image_file"] = "uploaded.d64"
                drive["image_path"] = "/Temp"
            drive["enabled"] = True
            return _ok()
        if action == "remove":
            drive["image_file"], drive["image_path"] = "", ""
            return _ok()
        if action == "reset":
            return _ok()
        if action == "on":
            drive["enabled"] = True
            return _ok()
        if action == "off":
            drive["enabled"] = False
            return _ok()
        if action == "set_mode":
            if params.get("mode") not in ("1541", "1571", "1581"):
                return _err(400, "Invalid mode")
            drive["type"] = params["mode"]
            return _ok()
        if action == "load_rom":
            if method == "PUT" and not params.get("file"):
                return _err(400, "Missing parameter 'file'")
            return _ok()
        return _err(404, "Unknown route")

    def _runner(self, method: str, action: str, params: dict[str, Any], body: bytes | None) -> tuple[int, Any, str]:
        if action not in ("sidplay", "modplay", "load_prg", "run_prg", "run_crt"):
            return _err(404, "Unknown route")
        if method == "PUT":
            file = params.get("file")
            if not file:
                return _err(400, "Missing parameter 'file'")
            name = os.path.splitext(os.path.basename(str(file)))[0]
        elif method == "POST":
            if not body:
                return _err(400, "No attachment")
            name = "uploaded program"
        else:
            return _err(404, "Unknown route")
        self.menu_open = False
        if action == "load_prg":
            self.loaded = name
        elif action in ("sidplay", "modplay"):
            self._start_program(f"{'SID' if action == 'sidplay' else 'MOD'}: {name}")
        else:
            self._start_program(name)
        return _ok()
