# Architecture

```
 Browser (React/Vite)  ── REST /api/* ──┐          ┌── MCP server (stdio / HTTP) ── AI agents
   │  WebSocket /ws (status, audit,     │          │   (calls /api/* with X-C64-Source: mcp)
   │  launch, session, scan, vision)    ▼          ▼
   │                          FastAPI (app/main.py, app/api/*)
   │                                   │
   │          ┌────────────────────────┼─────────────────────────────┐
   │          ▼                        ▼                             ▼
   │   CommandRouter  ◀── Intent ── CommandEngine            Library (SQLAlchemy)
   │   (services/commands.py)   (rules → optional LLM)       scanner / repository
   │          │                                                     │
   │          ▼                                                     ▼
   │   Launcher / Session ──────────────────────────────▶ SQLite (Postgres‑ready)
   │          │
   │          ▼
   │   DeviceService ── CapabilityMatrix (probed) ── Input / Menu / Drives / Runners / Streams
   │          │
   │          ▼
   │   UltimateClient (typed, documented routes only)
   │          │
   │     Transport: HttpTransport ─────────▶ C64 Ultimate  (X-Password header)
   │                SimulatedTransport ────▶ SimulatedUltimate (SIMULATE_C64)
   │
   └── MJPEG /api/streams/video.mjpeg, PCM /ws/audio ◀── StreamService ◀── UDP 11000/11001
```

## Layers

| Layer | Module | Responsibility |
|---|---|---|
| Transport | `ultimate/transport.py` | HTTP (httpx) or in‑process simulator; same response type |
| Client | `ultimate/client.py` | One method per documented route; typed results; 403/404 mapped to exceptions |
| Capabilities | `ultimate/capabilities.py` | Probes routes, 4‑state matrix, learns from real calls |
| Input | `ultimate/input.py` | REST keyboard/joystick; guarded legacy keyboard buffer; release‑all safety |
| Menu | `ultimate/menu.py` | 2000‑byte parser, closed‑loop navigation |
| Drives/Runners | `ultimate/drives.py`, `runners.py` | Capability‑gated wrappers; device paths vs. uploads |
| Streams | `ultimate/streams.py` | UDP receive, frame assembly, JPEG/PCM adaptation |
| Library | `library/*` | Media parsers (D64/D71/D81/T64/SID/CRT/MOD), scanner, repository |
| Services | `services/*` | Device lifecycle, launcher/session, command router, audit, events |
| AI | `ai/*` | Intent schema, rule parser, providers, engine, vision (experimental) |

The rest of the application only asks the **capability abstraction** (`caps.usable(...)`,
`caps.supported(...)`, `inputs.mode`) — never the firmware version.

## Safety model

* **No arbitrary HTTP from AI.** LLM output must validate into `ai/intents.py:Intent` (enum of intents,
  bounded fields). The router maps each intent to one approved service method.
* **No firmware / FPGA / update / delete routes exist in the client.** Flash‑writing config methods exist
  but are not reachable from any endpoint or intent.
* **Memory writes are internal.** The only caller is the legacy keyboard path, which first verifies the
  stock KERNAL IRQ vector ($0314 = $EA31), XMAX ($0289 = 10) and buffer count ($C6 ≤ 10), then writes
  only $0277–$0280 and $C6. Not exposed via API, command intents or MCP.
* **Inputs are always released:** on any input API failure (`release_all`), on launch failure, when a
  command fails with a device error, on reset/reboot/power off, when the last UI WebSocket that held
  input disconnects, by a watchdog after 20 s of holding, and on application shutdown.
* **Power off** needs an explicit confirmation (UI dialog or `confirm: true`), can be disabled with
  `ALLOW_POWER_OFF=false`, and is refused for MCP clients.
* **Source files are read‑only.** Scans never rename/move/delete; local disk images are uploaded
  read‑only (`mode=readonly`) when mounted.
* **Secrets** are registered with a logging filter and replaced by `***`; the settings API returns only
  `*_SET` flags; audit entries are redacted before storage.

## Launch flow (disk image)

1. Mount on drive A (turn the drive on first if the drives API reports it off).
2. Reset (unless the title’s `resetBeforeLoad` is off).
3. Wait for `READY.` by reading screen RAM via DMA (falls back to a timed wait if memory read is absent).
4. Type the load command (`LOAD"*",8,1` or per‑title override) — REST input, or the guarded keyboard buffer.
5. Wait for a new `READY.` below the LOAD line (errors like `?FILE NOT FOUND` abort) — legacy mode instead
   queues `RUN` in the KERNAL buffer, which BASIC executes when the load returns.
6. Type `RUN`; optionally press fire on the title’s joystick port after `startupDelay`.

Each step is published live as a launch job and recorded in the audit log.
