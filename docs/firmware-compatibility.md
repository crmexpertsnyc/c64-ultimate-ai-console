# Firmware compatibility & capability probing

Reference: <https://1541u-documentation.readthedocs.io/en/latest/api/api_calls.html>

The console never infers features from a version string. On connect it probes routes directly and
builds a matrix with four states:

| State | Meaning | Used for actions? |
|---|---|---|
| **supported** | A probe or real call proved the route exists | yes |
| **unsupported** | Route‑level 404/405/501 | no — shown as unsupported, never faked |
| **unverified** | Documented, but probing would change machine state | on explicit user request; first success → supported, 404 → unsupported |
| **unknown** | Device unreachable, 403, or ambiguous answer | no |

## Probes (all side‑effect free)

| Capability | Probe |
|---|---|
| version / info | `GET /v1/version`, `GET /v1/info` |
| menuScreen | `GET /v1/machine:menu_screen` — 200 = supported; a 404 whose body mentions the menu = supported (menu closed); a bare 404 is *ambiguous* (documented for “no menu active”) → unknown until a read succeeds |
| directKeyboard / directJoystick | `GET /v1/machine:input` (read‑only state) |
| memoryRead | `GET /v1/machine:readmem?address=0000&length=1` |
| memoryWrite | `PUT /v1/machine:writemem` **without parameters** — rejected by validation, nothing written |
| driveStatus | `GET /v1/drives` |
| mountDisk | `PUT /v1/drives/a:mount` without `image` — rejected, nothing mounted |
| loadPrg / runPrg / runCrt / sid / mod | `PUT /v1/runners:<x>` without `file` — rejected |
| video / audio stream | `PUT /v1/streams/<x>:stop` — stopping an idle stream is a no‑op |
| configApi | `GET /v1/configs` |
| fileApi | `GET /v1/files/Temp:info` |
| ftp / telnet | TCP connect to ports 21 / 23 (banner only) |
| reset, reboot, pause, resume, power off, menu button, drive control/ROM/mode | **not probed** → unverified |

The evidence (HTTP status + body excerpt) for each decision is shown in **Settings → Capabilities** and
in `/api/capabilities`, so you can check a verdict against your device.

## Commodore firmware 1.1.0 / Spiffy (“legacy”)

Expected: REST v0.1 core (runners, drives, machine reset/menu_button, readmem/writemem, configs) but no
`machine:input` and no `machine:menu_screen`.

* **Keyboard:** legacy mode injects PETSCII into the KERNAL keyboard buffer ($0277, count in $C6) in
  chunks of ≤ 10, waiting for the editor to drain each chunk. Before every chunk it verifies that the
  stock KERNAL IRQ handler is installed and the buffer variables are sane; otherwise it refuses (a running
  game owns the machine). Taps only — holding keys, RUN/STOP and RESTORE are unavailable.
  Functions: `legacy_type_text()`, `legacy_press_return()`, `legacy_load_command()`.
* **Joystick:** not possible through the firmware (no safe documented technique; the UI and API say so).
  Add the ESP32 [joystick bridge](joystick-bridge.md): when it is online it carries all joystick input,
  on any firmware (`input.joystickVia` = `bridge` in `/api/device/status`).
* **Menu:** can be toggled with `menu_button`, but cannot be read or verified.
* **Disk launch:** mount → reset → READY. check → `LOAD"*",8,1` via buffer → `RUN` queued in the buffer.

### Observed on a real C64 Ultimate (firmware 1.1.0s2, FPGA 122, core 1.49)

* `GET /v1/version` → `0.1`; `/v1/info` works; no network password by default.
* Unknown routes return **HTTP 404 with an empty body**. `machine:input` and `machine:menu_screen` return
  exactly that, so the prober compares every 404 against a request to a route that certainly doesn't exist
  (`/v1/machine:c64console_nonexistent_probe`) — identical → unsupported. This resolves the menu‑screen
  ambiguity without opening the menu.
* Missing parameters produce HTTP 400 with messages like `"Function run_prg requires parameter file"`.
* `GET /v1/drives` puts the **full path in `image_file`** (e.g. `/Temp/game.d64`) and leaves `image_path`
  empty; the client splits it. Drives reported: `a`, `b`, `IEC Drive` (with `last_error`), `Printer Emulation`.
* Streams (`video`/`audio` `:stop`), configs, files, FTP (21) and Telnet (23, the remote menu UI) respond.
* With a game running, `$0314/$0315` read `00 00` and `$0289` reads `00`, so the legacy keyboard path
  refuses to write — as designed. Typing works only at the BASIC `READY.` prompt (e.g. after a reset).

### Telnet remote menu (works on 1.1.0s2)

Port 23 serves the Ultimate's full menu as VT100/ANSI text. The console relays it to an xterm.js
terminal (Menu page, and the "☰ Ultimate menu" panel on the Display page) via `WS /ws/telnet`, removing
Telnet negotiation. Verified keys: cursor keys/WASD, RETURN (context menu), Backspace (0x08) = RUN/STOP,
F1 `ESC O P` action menu, F2 `ESC O Q` settings, F3 `ESC O R` / F5 `ESC [15~` page up/down,
F4 `ESC O S` system info, F6 `ESC [17~` Internet file search, F7 `ESC [18~` help. Info screens close with
RETURN/Space. Only the user's keystrokes are sent.

### Firmware with network input (researched 2026-09-26)

`POST /v1/machine:input` and `GET /v1/machine:menu_screen` arrived in Gideon's Ultimate firmware **3.15**
(Sept 2026), listed for U2/U2+/U2+L/U64/U64E2 — **not** the Commodore 64 Ultimate. The latest official
Commodore firmware is **1.1.0** (March 2026) and the latest Spiffy is **1.1.0s2**; neither has the routes.
Commodore warns that firmware built for other boards may not be safe on the C64U and isn't covered if it
bricks the unit. Recommendation: don't flash U64 builds; wait for a Commodore 1.2.x or a 3.15‑based Spiffy.
The console detects the routes automatically when they appear.
Sources: ultimate64.com/Firmware, github.com/GideonZ/1541u-documentation/pull/27, commodore.net/downloads,
github.com/spiffycrew/Spiffy_Ultimate.

## Newer firmware (“modern”)

* `POST /v1/machine:input` — keyboard (`tap/press/release`, 1–8 keys per event), joystick ports 1/2
  (`up/down/left/right/fire/fire2/fire3`), `release_all`. Up to 64 events / 4096 bytes per request.
* `GET /v1/machine:menu_screen` — 2000 bytes: 1000 characters (bit 7 = reverse) + 1000 colour bytes
  (low nibble foreground, high nibble background). The docs do not say whether characters are ASCII or C64
  screen codes, so the parser detects it (whichever interpretation yields more letters) and reports the
  choice as `encoding` — check this against your device the first time.

## Things to verify on real hardware

These follow the documentation but have only been exercised against the simulator:

1. Upload encoding for `POST` runners/mount (default raw `application/octet-stream`; switch to
   `C64_UPLOAD_MODE=multipart` if your firmware expects a form attachment).
2. Menu key mapping inside the Ultimate UI (cursor keys, RETURN, RUN/STOP, F1/F7 for paging, cursor‑left
   for “back”) — the closed loop reports “no change” rather than guessing if a key is ignored.
3. `menu_screen` character encoding (see above).
4. Timing of REST key taps (`C64_TYPE_DELAY_MS`, default 60 ms per key).
5. The 404 body text for unknown routes on your firmware (affects how quickly ambiguous probes resolve).
