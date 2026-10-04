# Developer API

Interactive docs: `/docs` (Swagger UI), `/redoc`, raw schema `/openapi.json` — generated automatically.

Conventions

* JSON in/out. Errors: `{"detail": "...", "kind": "unsupported|unsafe_state|launch|device_auth|device_unreachable|device_error|invalid"}`
  with 409 for “not possible on this device/state”, 502/503 for device problems, 400 for bad input.
* `X-C64-Source: ui | api | mcp` labels the audit entry (default `api`).
* No authentication is built in — run it on a trusted LAN or behind a reverse proxy with auth.

## Device

| Method | Path | Body / notes |
|---|---|---|
| GET | `/api/device` | status, info, drives, input mode, capabilities, session |
| GET | `/api/capabilities` | probed matrix (`capabilities` strict booleans, `usable`, `details` with evidence) |
| POST | `/api/device/connect` | reconnect + re‑probe |
| POST | `/api/device/reset` · `/reboot` · `/pause` · `/resume` | releases inputs first (reset/reboot) |
| POST | `/api/device/power-off` | `{"confirm": true}` required |
| GET | `/api/device/menu` | `{open, screen}` parsed menu |
| POST | `/api/device/menu` | `{"action": "open|close|toggle|up|down|left|right|return|back|exit|home|page_up|page_down"}` |
| POST | `/api/device/input` | `{"key": "return", "transition": "tap|press|release"}`; combos `left_shift+a` |
| POST | `/api/device/type` | `{"text": "LIST", "press_return": true}` |
| POST | `/api/device/joystick` | `{"inputs": ["up","fire"], "port": 2, "transition": "press"}` |
| POST | `/api/device/joystick/port` | `{"port": 1}` default port |
| POST | `/api/device/release-all` | emergency release |
| GET | `/api/device/drives` | drive list |
| POST | `/api/device/drives/{a}/mount` | `{"path": "/Usb0/x.d64", "storage": "device"}` or a local path with `"storage": "local"` (uploaded read‑only) |
| POST | `/api/device/drives/{a}/mode` | `{"mode": "1541|1571|1581"}` |
| POST | `/api/device/drives/{a}/{remove|reset|on|off}` | |
| POST | `/api/device/run` | `{"kind": "run_prg|load_prg|run_crt|sid|mod", "path": "/Usb0/…"}` (file on the Ultimate) |
| GET | `/api/device/screen` | C64 text screen via DMA read (if supported) |

## Library & session

| Method | Path | Notes |
|---|---|---|
| GET | `/api/library` | `q, favorites, recent, format, publisher, year, genre, category, multiplayer, joystick_port, limit, offset` |
| GET | `/api/library/facets` · `/api/library/stats` | |
| POST | `/api/library/scan` | `{"paths": ["/games"]}` (background; progress on `/ws` as `scan`) |
| GET | `/api/games/{id}` · PATCH `/api/games/{id}` | editable: title, alternateNames, publisher, year, genre, category, joystickPort, players, preferredLaunch, loadCommand, runAfterLoad, resetBeforeLoad, startupDelay, loadTimeout, needsFire, notes, tags, coverUrl, favorite |
| POST | `/api/games/{id}/play` | `{"disk": 1, "method": null, "wait": false}` → launch job |
| POST | `/api/games/{id}/mount` | `{"disk": 2}` mount without launching |
| POST | `/api/games/{id}/favorite` | toggle |
| GET | `/api/current-session` | current game, disks, current disk, last job |
| POST | `/api/session/disk/{n}` · `/api/session/next-disk` · `/api/session/previous-disk` | |
| GET | `/api/jobs` · `/api/jobs/{id}` | launch jobs with steps |

Launch methods: `auto, run_prg, load_prg, run_crt, mount_and_load, mount_only, dma_first_prg, t64_extract, sid, mod`.

## Commands

| Method | Path | Notes |
|---|---|---|
| POST | `/api/command` | `{"text": "Play Bruce Lee", "context": {"selectedGameId": 3, "menuOpen": false}, "confirm": false}` |
| POST | `/api/command/interpret` | dry run: returns the intent only |
| POST | `/api/intent` | execute a structured intent: `{"intent": {"intent": "JOYSTICK_INPUT", "joystick": ["fire"], "port": 2}}` |
| GET | `/api/command/examples` | |

## Other

`GET /api/audit`, `GET /api/troubleshooting`, `GET|PUT /api/settings`, `POST /api/setup/test-connection`,
`GET /api/ai`, `POST /api/ai/test`, `GET /api/streams`, `POST /api/streams/{video|audio}/{start|stop}`,
`GET /api/streams/video.mjpeg`, `GET /api/streams/video/frame.jpg`, `WS /ws/audio`,
`/api/assembly64/*`, `/api/vision*`, `GET /api/fs/dirs`.

## WebSocket `/ws`

Messages: `{"type": "status"|"audit"|"audit_backlog"|"launch"|"session"|"scan"|"vision", "data": …}`.
Clients may send `{"type": "release_all"}`. When a client disconnects while inputs are held, they are released.
