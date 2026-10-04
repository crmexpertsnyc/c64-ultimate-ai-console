# 📟 BBS directory and browser terminal

**Discover → BBS** lists public telnet bulletin board systems. You can search them, keep favorites and notes, and
connect from the browser. A **☎ On your C64** tab explains how to call a board from the real C64 through the C64
Ultimate's modem emulation.

## Using it

- **Directory.**
  - Search by name, description or location (the host is searched too).
  - Filters: PETSCII, ANSI/ASCII, ★ Favorites, and 🔌 Recently reachable (answered a check in the last 30 days).
  - Each card shows the board's name, description, `host:port`, terminal compatibility, website and last check.
- **Details.**
  - Source links, each with the date it was seen.
  - Connection instructions (browser, telnet client, real C64).
  - ⭐ Favorite and 📝 notes, kept per family profile.
  - **Copy connection details** and **Connect in browser**.
- **Browser terminal** (`/bbs/{id}/terminal`):
  - Toolbar: connect/disconnect and a status pill.
  - Mode override (PETSCII or ANSI, for this session only).
  - Clear screen, display size (PETSCII 1–4× or Fit; ANSI font size).
  - **⌨ Keys**: an on-screen keyboard for phones.

## Compatibility and reachability: what the labels mean

| Label | Meaning |
|---|---|
| **✓ confirmed** | An admin connected and checked it. Only set by hand (🛡 Admin in a board's details). |
| **? unverified** | A source lists it that way, but it hasn't been tested. PETSCII becomes "unverified" only when one of these holds: SyncTERM's entry sets `ScreenMode=C64`, the listing literally says "PETSCII", or the listed software is a Commodore BBS package (Image BBS, Color 64, C-Net, DMBBS…). Mentioning Commodore or the C64 is **not** enough. |
| **– unknown** | Nothing says either way. |

- **Answered / No answer.** A TCP connection to the board's address succeeded or failed.
  - "Answered" means *something* accepted the connection. It doesn't prove the board is fully working.
  - The last successful check is kept separately from the latest result.

## Sources (checked October 2026)

| Source | Use | Default |
|---|---|---|
| [SyncTERM directory](https://syncterm.bbsdev.net/syncterm.lst) | The dialing directory the SyncTERM project publishes for its users to download: an INI file with ConnectionType, Address, Port, Comment and an optional ScreenMode. Only telnet/raw entries are used; SSH and rlogin are skipped. | **on** (`BBS_SOURCE_SYNCTERM`) |
| [Telnet BBS Guide](https://www.telnetbbsguide.com) | The largest monthly list (`bbslist.csv` in the monthly ZIP). Its terms ask software authors to **request permission** before including the list, and forbid merging it into another publication without written consent. | **off** (`BBS_SOURCE_TBG`): switch it on only once you have permission (info at telnetbbsguide dot com) |
| [The Oasis BBS Commodore listing](https://theoasisbbs.com/commodore-bbs-listing/) | A hand-kept list of about two dozen active Commodore 64/128 (and a few Amiga) boards: one table per board with name, sysop, software and telnet address. No feed or API, and the site says "All Rights Reserved". Amiga software is never taken as a sign of PETSCII. | **off** (`BBS_SOURCE_OASIS`): switch it on once The Oasis BBS says it's fine |

- **What a record keeps.** Each record keeps its source URL and the date it was seen. Nothing is invented: names, addresses and descriptions come only from the sources, or from an admin who adds a board by hand.
- **Deduplication.** Boards are merged by normalized host and port.
- **When a source fails.** If no source has ever been imported, the page shows **"Directory setup pending"**.
- **Fixtures.** Development fixtures (`DEV_FIXTURES` in `bbs_sources.py`) use the reserved `.invalid` domain and are never imported by the app.

## Admin

- **Who counts as admin.**
  - This computer, or a device signed in with the console password.
  - With no password set, only this computer.
  - Everyone else sees approved boards only.
- **Approval.** Newly imported boards are **pending**. The browser terminal opens only **approved** boards.
- **🛡 Manage tab.**
  - Refresh now.
  - Switch sources on/off, with their terms shown.
  - **Approve all pending boards that answered their last check** (asks first; unreachable or unchecked boards stay pending).
  - Add a board by hand. It starts as pending; add a link to where you found it.
- **Per board** (in Details): approve/reject, protocol (telnet or raw TCP), PETSCII/ANSI compatibility, and "Check now".

## Schedules (Settings → Sources & updates)

- **BBS directory**: monthly (`30d`); can be changed or switched off.
- **BBS reachability**: daily.
  - Each run checks up to 25 boards, 2 s apart, with a 5 s timeout.
  - A board is checked at most weekly.
  - Failing boards back off: 2, 4, 8 … up to 60 days.
  - No logins and no data: connect, then close.

## Relay security

The browser never connects to a board directly. It opens `/ws/bbs/{board_id}` on the console, and the console makes
the TCP connection.

- **Approved board ids only.** The browser never supplies a host or port.
- **Destination checks.**
  - The host is resolved server-side, and **every** address it resolves to must be public unicast.
  - Refused, IPv4: loopback, private (RFC 1918), CGNAT, link-local (incl. 169.254.169.254 cloud metadata), multicast, reserved, documentation, unspecified.
  - Refused, IPv6: ULA (`fc00::/7`, incl. `fd00:ec2::254`), link-local, documentation.
  - IPv4 hidden inside IPv6 is checked too: mapped (`::ffff:`), NAT64 (`64:ff9b::/96`), 6to4 (`2002::/16`). Teredo is refused outright.
- **Pinning (DNS rebinding).** The socket connects to the already-checked IP literal. DNS is not asked again.
- **Port deny list.** Ports that are never a BBS are refused: SSH, mail, DNS, HTTP(S), databases, RDP/VNC, Docker/Kubernetes APIs, …
- **Who may connect.**
  - The WebSocket Origin must match the console's own host (`X-Forwarded-Host` behind Tailscale Serve).
  - The console password applies when one is set (AuthGate on `/ws`).
- **Limits.**

  | Limit | Value |
  |---|---|
  | Sessions | 4 at once (`BBS_MAX_SESSIONS`), 2 per device |
  | Connection attempts | 10 per device per 10 minutes |
  | Connect timeout | 10 s |
  | Idle timeout | 20 min without typing (`BBS_IDLE_MINUTES`) |
  | Session length | 3 h maximum |
  | Data | 50 MB down / 2 MB up per session |
  | Input frames | 4 KB maximum |

- **Cleanup.** The backend socket is closed whenever the session ends: browser closed, board closed, a limit hit, or an error.
- **Privacy and rendering.**
  - Terminal input, output and transcripts are never logged or stored.
  - BBS passwords are not saved.
  - The UI says plainly that telnet is unencrypted.
  - Remote content is never rendered as HTML: PETSCII is drawn on a canvas, ANSI by xterm.js.

## Telnet

- **Negotiation.**
  - Accepts the server's ECHO, SGA and BINARY.
  - Offers TTYPE (answers `ANSI` or `PETSCII`) and NAWS (80×25 or 40×25).
  - Refuses everything else, and never re-answers an unchanged option, so negotiation can't loop.
- **Escaping.** `IAC IAC` is un-doubled on the way in. 0xFF is doubled and CR is sent as CR NUL (without BINARY) on the way out.
- **Raw TCP.** Boards that don't speak telnet (many real-C64 boards behind Wi-Fi modems) can be set to **raw TCP** by an admin. Bytes then pass untouched.

## PETSCII support: what works, what doesn't

**Supported**
- 40×25 screen, Pepto palette.
- Both character sets (switched by 0x0E/0x8E).
- The 16 colour codes, reverse on/off.
- Cursor up/down/left/right, HOME, CLR, INST/DEL, RETURN/shift-RETURN, scrolling, wrapping.

**Keyboard**

| Key | Sends |
|---|---|
| Letters | lower-case letters as 0x41–0x5A, capitals as 0xC1–0xDA |
| Backspace | DEL |
| Esc | RUN/STOP |
| Home / Shift+Home | HOME / CLR |
| F1–F8 | F1–F8 |
| Ctrl+1…8 | colours |
| Ctrl+9 / Ctrl+0 | reverse on / off |

The on-screen keys add the ← (back arrow), £ and ↑ keys.

**Not supported / untested**
- Flashing cursor modes, the C64 screen editor's line linking, and board tricks that rely on exact C64 timing.
- Sprites, sound, and graphics modes other than the text screen.
- Border/background colour changes (a board can't change them over PETSCII anyway).
- Shift-lock and C=-key graphics typing (use the on-screen keys or paste).

**ANSI mode.** xterm.js decodes CP437, so box drawing works, along with ANSI colours and cursor control. ANSI music, RIP graphics and Avatar are not supported.

**Not claimed.** Complete compatibility with every board is not claimed. Each board's compatibility stays "unverified" until someone checks it.

**Out of scope for this release:** AI summaries, saved credentials, automated posting, scraping BBS content, and file transfers (XMODEM/Punter).

## Calling from the real C64

- **Modem settings.**
  - The **☎ On your C64** tab shows the C64 Ultimate's *Modem Settings*, read only (e.g. Modem Interface `ACIA / SwiftLink`, ACIA `DE00/NMI`).
  - The console never changes firmware, network or modem settings.
- **Calling a board.**
  1. Load a SwiftLink terminal (CCGMS 2021 or StrikeTerm 2014).
  2. Choose SwiftLink/DE00.
  3. Type `AT` (expect `OK`).
  4. Dial with `ATDT host:port`.
  5. Hang up with `+++` then `ATH`.

**"Dial on my C64"** (the console typing the dial command for you) stays behind `BBS_DIAL_ON_C64=false`. It stays off until all four of these pass on real hardware:

1. The terminal program launches from the library on the real C64.
2. It finds the modem (AT → OK) with the Ultimate's ACIA/SwiftLink settings.
3. Text typed from the app (Controller → keyboard) arrives reliably, including `:` and digits.
4. ATDT to an approved board connects and shows its login screen.

## Configuration

| Setting | Default | |
|---|---|---|
| `BBS_SOURCE_SYNCTERM` | `true` | SyncTERM directory source |
| `BBS_SOURCE_TBG` | `false` | Telnet BBS Guide; permission needed |
| `BBS_SOURCE_OASIS` | `false` | The Oasis BBS Commodore listing; permission needed |
| `BBS_IDLE_MINUTES` | `20` | Idle timeout |
| `BBS_MAX_SESSIONS` | `4` | Concurrent terminal sessions |
| `BBS_DIAL_ON_C64` | `false` | Hardware dialing (see the checklist above) |

## Code

| Area | Files |
|---|---|
| Backend | `app/services/bbs.py` (directory, approval, checks, limits), `app/services/bbs_sources.py` (importers), `app/services/bbs_net.py` (destination policy, pinning, telnet), `app/api/bbs_api.py` (REST + relay), `app/models/bbs.py` |
| Frontend | `src/pages/BbsPage.tsx`, `src/pages/BbsTerminal.tsx`, `src/services/petscii.ts`, `src/services/bbsApi.ts` |
| Tests | `backend/tests/test_bbs.py`: parsers, dedup, favorites/notes, admin gating, the IPv4/IPv6 policy, rebinding/pinning, telnet negotiation, limits, and the relay end to end against a local mock telnet board (incl. cleanup and idle timeout). `frontend/tests/petscii.test.ts`: rendering and keyboard (`npm test`). |

## Attribution

| Asset | Author | License |
|---|---|---|
| Unscii-8 font (`frontend/public/fonts/unscii-8.woff`) | viznut | public domain, http://viznut.fi/unscii/ |
| xterm.js (`@xterm/xterm`) | the xterm.js authors | MIT |

- The PETSCII → Unicode mapping follows Unicode's *Symbols for Legacy Computing* block.
- The C64 palette is Pepto's.
- Board data comes from the sources above, and each board links back to its source.
