# Milestones

Each milestone was closed with `ruff check`, `pytest`, `tsc -b`, `eslint` and `vite build` passing.

## M1 — Device connection & dashboard
`.env` + settings UI (`settings.json`); typed client for every documented v1 route; `X-Password` with log
redaction; `/v1/version` + `/v1/info`; capability prober (4 states with evidence); reset / reboot / pause /
resume / power off (confirmed); drive status; simulator (modern + legacy profiles); React dashboard with
live WebSocket status; setup wizard.

## M2 — Launching & library
D64/D71/D81 directory parser, T64 extraction, SID/CRT/MOD headers; recursive scanner with TOSEC‑aware
normalisation and multi‑disk grouping (sources never modified); SQLite via SQLAlchemy (PostgreSQL‑ready);
launcher (run_prg, run_crt, mount + reset + LOAD + RUN, DMA first PRG, T64, SID, MOD) with step‑by‑step jobs;
multi‑disk session (`mountDisk`, `nextDisk`, `previousDisk`); library browser and detail pages.

## M3 — Input & menu
REST keyboard/joystick with press/release/tap and key combos; guarded legacy keyboard‑buffer typing;
release‑all on failure, disconnect, watchdog and shutdown; menu_screen parser (encoding detection,
colours, reverse video, selection) and closed‑loop navigation; virtual joystick + full C64 keyboard.

## M4 — Natural language & AI
Intent schema; rule parser for all required intents; LLM fallback (vLLM / OpenAI‑compatible, OpenWebUI,
Ollama, Anthropic) with strict validation; command router; audit log with captured REST calls;
troubleshooting page.

## M5 — Streaming, MCP, automation
UDP video/audio adapter (MJPEG + WebAudio); MCP server (stdio + HTTP); Assembly64 tooling; vision‑mode
scaffold with governor; Docker image + compose.

## Not yet verified on real hardware
Everything above was tested against the simulator and unit tests only. See
[firmware-compatibility.md](firmware-compatibility.md#things-to-verify-on-real-hardware) for the behaviours
to confirm on a real C64 Ultimate (upload encoding, menu key mapping and character encoding, key timing,
stream packet handling). The Docker files were written but not built here (Docker is not installed on
this machine).
