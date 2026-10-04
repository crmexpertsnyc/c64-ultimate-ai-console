# C64 Ultimate AI Console

Turn a **Commodore 64 Ultimate** into a remotely controlled, AI‑assisted game console. Type (or speak)
“Play Bruce Lee”, “Put disk 2 in”, “Press fire”, “Open the Ultimate menu” — the console turns that into a
validated intent and calls the right **documented** Ultimate REST endpoints.

* Runs on Windows, Linux, macOS or a DGX Spark; talks to the Ultimate over your LAN.
* Uses only documented interfaces (REST, FTP read, TCP probes, UDP streams). **Never** flashes firmware,
  touches FPGA images, runs firmware updates, or deletes/renames your files.
* Detects what *your* firmware supports by probing — works on Commodore firmware 1.1.0 / Spiffy and on
  newer firmware with `machine:input` and `machine:menu_screen`.
* Works without any AI. Optional LLMs (vLLM, Ollama, OpenWebUI, OpenAI‑compatible, Anthropic) only produce
  **validated intents**; they can never issue HTTP calls or memory writes.

---

## Install (one command)

**Windows 10/11** (PowerShell):

```powershell
irm https://raw.githubusercontent.com/<owner>/<repo>/main/scripts/install.ps1 | iex
```

**Linux / macOS**:

```bash
curl -fsSL https://raw.githubusercontent.com/<owner>/<repo>/main/scripts/install.sh | bash -s -- --repo <owner>/<repo>
```

Both download the latest release (the web UI comes pre-built, so only Python 3.11+ is needed), install it,
start it at sign-in, and open `http://localhost:8064`. From a copy of this project run `scripts/install.ps1` or
`scripts/install.sh` instead. **Updates:** Settings → About & updates shows when a newer version is out
(checked daily); "Update now" on Windows, or `scripts/update.ps1` / `scripts/update.sh`. Your settings,
library and saves (`backend/data`) are kept. Releases are built by `.github/workflows/release.yml` when a
`v*` tag is pushed. (Replace `<owner>/<repo>` once the project is published.)

## Quick start (Docker)

```bash
cp .env.example .env        # set C64_ULTIMATE_HOST, LIBRARY_HOST_PATH
docker compose up -d --build
```

Open `http://<this-machine>:8064`. The **Setup Wizard** asks for the Ultimate’s IP, whether a network
password is set, tests the connection, shows firmware and capabilities, then scans your games.

* Games: `LIBRARY_HOST_PATH` (host folder) is mounted **read-only** at `/games`; add `/games` in the wizard.
* Data (SQLite DB, `settings.json`, downloads) persists in `./data`.
* Streaming: set `STREAM_TARGET_HOST` to the Docker host’s LAN IP (or use `network_mode: host` on Linux).
* MCP server for agents: `docker compose --profile mcp up -d` (streamable HTTP on `:8065/mcp`).

## Quick start (no Docker)

Requirements: Python 3.11+ and Node 20+.

```bash
# Backend
cd backend
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev,mcp]"
cd ../frontend && npm ci && npm run build && cd ../backend
uvicorn app.main:app --host 0.0.0.0 --port 8064
```

The backend serves the built UI from `frontend/dist`. For UI development run `npm run dev` in `frontend/`
(Vite on :5173 proxies `/api` and `/ws` to :8064), or use `scripts/dev.sh` / `scripts/dev.ps1`.

### Try it without hardware

```bash
SIMULATE_C64=true uvicorn app.main:app --port 8064            # modern firmware
SIMULATE_C64=true SIMULATE_PROFILE=legacy uvicorn app.main:app  # Commodore 1.1.0 / Spiffy behaviour
```

Or pick **Try simulation mode** in the wizard. The simulator implements the REST routes, a small BASIC
screen editor (LOAD/RUN/SYS, READY.), drives, runners and an Ultimate‑style menu.

> **Windows note:** if the project lives under a very long path (> ~200 characters), compiled Python
> extensions may fail to load (`DLL load failed … filename or extension is too long`). Keep the project in
> a short path such as `C:\dev\c64-ai-console`.

## Configuration

All settings live in `.env` (see [.env.example](.env.example)) and can be edited in **Settings**.
UI edits are stored in `DATA_DIR/settings.json` (created with owner‑only permissions) and override `.env`.

| Setting | Purpose |
|---|---|
| `C64_ULTIMATE_HOST` / `_PORT` / `_PROTOCOL` | Device address (port 80 by default) |
| `C64_ULTIMATE_PASSWORD` | Network password → `X-Password` header; redacted from logs, UI and audit |
| `SIMULATE_C64`, `SIMULATE_PROFILE` | Simulation mode (`modern` / `legacy`) |
| `LIBRARY_PATHS` | Game folders (`;`‑separated); local paths, mounted shares or UNC paths |
| `AI_PROVIDER`, `AI_BASE_URL`, `AI_MODEL`, `AI_API_KEY` | Optional LLM (`none` default) |
| `STREAM_TARGET_HOST` | Where the Ultimate sends UDP video/audio (auto‑detected) |
| `VISION_ENABLED` + limits | Experimental AI vision control (off) |
| `DATABASE_URL` | SQLite by default; PostgreSQL via `postgresql+psycopg://…` + `pip install ".[postgres]"` |

## Using it

* **Console** — connection, firmware, current game/disk, drives, joystick mode, AI model, recent actions,
  big PLAY / RESET / MENU / DISK / JOYSTICK / KEYBOARD / POWER buttons and the *Ask your C64…* bar (🎙 uses
  the browser’s speech recognition where available; press `/` to focus).
* **Catalog** — search the Assembly64 online catalog (Gamebase64, OneLoad64, CSDB, HVSC …) and play or
  add titles; “Play <title>” uses it automatically when a title isn't in your library
  (needs `ASSEMBLY64_CLIENT_ID`, see [docs/assembly64.md](docs/assembly64.md)).
* **Library** — search, favorites, recently played, format/publisher/year/genre/players/port filters,
  cover art (URL per title, or a generated tile). Title pages have Play, Mount, disk selector, Favorite,
  metadata editing and the disk directory.
* **Controller** — joystick (true press/release, port 1/2, optional arrow keys + space), full C64 keyboard
  with latching SHIFT/C=/CTRL and hold‑to‑hold keys, text typing. **RELEASE ALL** is always in the top bar.
* **Menu** — the Ultimate's own menu over Telnet in a browser terminal (works on current C64U firmware);
  on firmware with `menu_screen` also a live 40×25 view with closed‑loop navigation.
* **Display** — the C64 at 50 fps with sound; Play buttons jump here and start picture + sound
  automatically. **TV mode** (double‑click / F), **screenshots** (📷 / S), **⏱ latency readout**,
  the Ultimate menu side panel, and **Go live** (Twitch/YouTube RTMP) / **⏺ Record** (MP4).
* **Gallery** — screenshots and recordings: view, share, download, use a screenshot as cover art.
* **Cover art** — real box art and title screens from the libretro thumbnails collection (~2,800 C64
  titles), CSDB screenshots that pass a quality check, and otherwise the best in‑game frame captured while
  you play (loading/BASIC screens are skipped). Library → **🖼 Find cover art** fills in existing titles; a
  cover you choose yourself is never replaced. Set `COVER_ART_ONLINE=false` to keep everything offline.
* **OBS** — add a Browser Source with `http://<host>:8064/stream-view` for a chrome‑free picture + sound.
* **Gamepad** — a USB/Bluetooth controller navigates the app (D‑pad/stick, A select, B back, Start =
  Display) and drives the remote Ultimate menu; on firmware with network input it's the C64 joystick.
* **Logs** — full audit log (source, command, intent, REST calls, result, duration), recent REST calls,
  hints, diagnostics download.
* **Assembly64** — read `server.json` (FTP, read‑only), generate an entry for a Home Assembly 64 server,
  browse such a server and import files into the library.

### Natural‑language commands

Rule‑based parsing handles (no AI needed): *Play/Load/Launch X*, *Search/Find X*, *Put disk 2 in*,
*Next/Previous disk*, *Reset the C64*, *Reboot*, *Power off* (asks to confirm), *Pause/Resume*,
*Open/Close the menu*, *What's on the menu?*, *Menu down*, *Press return/F1/run stop*, *Type SYS 49152
[and press return]*, *Press fire on joystick 2*, *Hold up and fire*, *Use joystick port 1*, *Release all*,
*Play the SID file Commando*, *Play the MOD Space Debris*, *Launch this PRG* (the title open in the UI),
*What's mounted?*, *What's playing?*, *Show device info*. Unknown phrasing falls through to the optional LLM.

## Developer API & MCP

OpenAPI docs: `http://<host>:8064/docs` (Swagger) and `/redoc`. Highlights:

```
GET  /api/device                GET  /api/capabilities         GET  /api/library?q=…
GET  /api/games/{id}            POST /api/games/{id}/play      POST /api/device/reset
POST /api/device/menu           POST /api/device/input         POST /api/device/type
POST /api/device/joystick       POST /api/session/disk/{n}     POST /api/command
POST /api/intent                GET  /api/current-session      WS   /ws  (live events)
```

Send `X-C64-Source: <ui|api|mcp>` to label audit entries. See [docs/api.md](docs/api.md).

**AI agents (MCP):** the console serves an MCP endpoint at `http://<host>:8064/mcp` with 23 safe tools
(play, search, see the screen, disks, record…). **Settings → Connect AI agents** gives copy‑ready setup for
Claude Code, Claude Desktop, VS Code, Cursor and MCP Inspector; full guide in [docs/mcp.md](docs/mcp.md).

## Quality gates

```bash
scripts/check.sh      # ruff + pytest (backend), tsc + eslint + vite build (frontend)
```

## Documentation

* [docs/architecture.md](docs/architecture.md) — layers, safety model, data flow
* [docs/firmware-compatibility.md](docs/firmware-compatibility.md) — capability probing, legacy mode
* [docs/api.md](docs/api.md) — developer API
* [docs/streaming.md](docs/streaming.md) — video/audio stream adapter
* [docs/assembly64.md](docs/assembly64.md) — Spiffy `server.json`, Home Assembly 64
* [docs/mcp.md](docs/mcp.md) — MCP server
* [docs/joystick-bridge.md](docs/joystick-bridge.md) — ESP32 joystick bridge: play games from the PC/phone/gamepad on any firmware
* [docs/ai.md](docs/ai.md) — AI providers and vision mode
* [docs/bbs.md](docs/bbs.md) — 📟 BBS directory, browser telnet terminal (PETSCII/ANSI), relay security, sources and attribution
* [docs/milestones.md](docs/milestones.md) — what was built per milestone, and what is not verified yet
* [docs/roadmap.md](docs/roadmap.md) — product direction, TeensyROM / multi-device plans
