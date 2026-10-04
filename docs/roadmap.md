# Roadmap

Product direction: a **lightweight, browser-based way to play, capture and stream Commodore software**
from any screen (PC, tablet, phone, TV). Engineering tools (memory/BASIC/config editors, file
managers, debug tracing) are intentionally out of scope.

## Shipped

| Area | Feature |
|---|---|
| Play | Natural-language + voice commands, local library, Assembly64 online catalog with auto-fetch, multi-disk sessions, boot-program detection, ZIP releases |
| Watch | 50 fps display with measured latency, sound, **TV mode** (fullscreen, auto-hiding controls) |
| Capture | Pixel-perfect **screenshots** + gallery (share/download/use as cover), automatic cover art, CSDB box art |
| Stream | **Go live** to Twitch/YouTube (RTMP), **Record** to MP4, OBS **stream view** page |
| Control | Telnet **remote Ultimate menu** (works on current C64U firmware), **gamepad** navigation; **ESP32 joystick bridge** (joystick from PC keyboard, gamepad, phone and AI agents on any firmware; hardware not yet built/verified); **Open on phone** QR code |
| Smart | Optional private LLM (e.g. gpt-oss on a DGX Spark) to understand descriptions; MCP server for agents |

## Business track (suggested order)

1. **Open-source the home app.** Add a license, clean out secrets and personal data (hosts, keys, saved
   settings, the database, save states, logs, screenshots), and write install docs.
2. **Launch the website as the public C64 hub.** News, new releases, videos, the hardware shop and guides. It reuses
   the app's news monitor (`app/services/news.py`) and shop catalog (`app/services/shop.py`, whose catalog JSON the
   website publishes through `SHOP_CATALOG_URL`). Checkout, seller accounts and affiliate tracking live on the
   website, never in the home app.
3. **"Connect my console".** An outbound link from the home app to the website, plus accounts and sync. Remote access
   becomes a paid tier and replaces the Tailscale setup for regular users.

## Next

### 1. Multi-device: one console, many Commodores
Grow from "C64 Ultimate console" into a hardware-agnostic front end:

```
                 ┌── Commodore 64 Ultimate        (REST API, UDP streams, Telnet menu)   ✅ today
                 ├── Ultimate 64 / Elite / II     (same REST API; firmware 3.15 adds input) ✅ mostly today
Console (web) ───┼── Original C64/C128 + TeensyROM (TeensyROM-Web / serial protocol)       ⏳ planned
                 └── MiSTer FPGA (C64 core)        (to be investigated)                     🔎 research
```

The backend already isolates hardware behind a transport + typed client + **capability matrix**, and
the rest of the app only asks "can this device do X?". A new device type means a new client adapter
that reports its capabilities; library, catalog, commands, AI, gallery and streaming stay as they are.

### 2. TeensyROM integration (original C64 / C128)
[TeensyROM](https://github.com/SensoriumEmbedded/TeensyROM) is a cartridge that adds USB/Ethernet/MIDI
to real C64/C128 hardware. [TeensyROM-Web](https://github.com/MetalHexx/TeensyROM-Web) (MIT, .NET 9 +
Angular) already controls it from a browser: it discovers devices over TCP port 2112 or USB serial,
launches games/demos/SIDs/images from the cartridge's SD/USB storage, and exposes a REST API (with
SignalR for live updates, docs at `/scalar/v1`).

Plan:
1. **Adapter via TeensyROM-Web's REST API** (fastest path): list storage, launch file, SID playback
   controls, device list. Map to our capability matrix (launch PRG/CRT/disk/SID ✅; memory, streams,
   Ultimate menu ❌).
2. **Upload-and-launch** for catalog/library titles not on the cartridge's storage, if the API
   allows transfers.
3. Later, optionally speak TeensyROM's serial/TCP protocol directly to avoid running a second server.

Caveats to resolve: no video/audio stream from a real C64 (capture would need a USB video grabber),
and keyboard/joystick injection availability (not stated in TeensyROM-Web's docs).

### 3. MiSTer FPGA (research)
Investigate remote-launch options for the MiSTer C64 core (e.g. MiSTer Remote / MiSTer's command
interfaces) and whether video/audio can be captured for streaming.

### 4. Play polish
- SID jukebox basics: next/previous tune, subsongs, simple queue.
- Find the Ultimate automatically on the network in the setup wizard.
- Box art from Gamebase screenshots where licensing allows.
- Private voice recognition via a Whisper model on the Spark (LiteLLM), replacing browser speech.

### Waiting on firmware
- Keyboard/joystick injection on the C64 Ultimate (`machine:input`, Ultimate firmware 3.15 lists it
  for U64-class hardware only; no C64U build yet). The on-screen joystick/keyboard and gamepad
  → joystick mapping are already built and switch on automatically when available.
