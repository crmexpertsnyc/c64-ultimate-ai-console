# Third-party software and assets

The console's own code is MIT licensed (see `LICENSE`). It uses, bundles at build time, or downloads on request
the following, each under its own license.

## Bundled in builds and releases

| Component | Where | License |
|---|---|---|
| [EmulatorJS](https://github.com/EmulatorJS/EmulatorJS) player (`@emulatorjs/emulatorjs`) | copied into `frontend/public/emulator/data` at build time by `frontend/scripts/copy-emulator.mjs` (not stored in this repository) | GPL-3.0 |
| [VICE](https://vice-emu.sourceforge.io/) C64 core for EmulatorJS (`@emulatorjs/core-vice_x64sc`) | same | GPL-2.0-or-later; it contains the C64 system ROMs as distributed with VICE |
| EmulatorJS's archive extractors (incl. unRAR) | same | see their files in the EmulatorJS package (unRAR has its own license that forbids recreating the RAR compressor) |
| [xterm.js](https://xtermjs.org/) (`@xterm/xterm`) | BBS ANSI terminal | MIT |
| [Unscii](http://viznut.fi/unscii/) 8×8 font (`frontend/public/fonts/unscii-8.woff`) | BBS PETSCII terminal | Public domain (viznut) |
| React, React Router, Vite and other npm packages | web UI | see `frontend/package.json` (MIT and similar) |
| FastAPI, SQLAlchemy, httpx, Pillow and other Python packages | backend | see `backend/pyproject.toml` (MIT, BSD, Apache-2.0, HPND) |

The EmulatorJS and VICE sources are available from their projects linked above; the exact versions are pinned
in `frontend/package-lock.json`.

## Downloaded only when you ask for it

| Component | When | License / notes |
|---|---|---|
| [CCGMS Future](https://github.com/mist64/ccgmsterm) v0.2 (`ccgms.prg`) | BBS → On your C64 → "Add CCGMS" (checksum-pinned) | continuation of Craig Smith's freeware CCGMS terminal; see the project |
| Game files, SID tunes, magazine scans, cover art | when you import, play or browse them | owned by their authors; fetched from the sources shown in the app (CSDb, Assembly64/HVSC, Internet Archive, libretro thumbnails) |
| BBS lists | monthly, from sources you enable | each list's own terms, shown in BBS → Manage |

## Data

The PETSCII → Unicode mapping follows Unicode's "Symbols for Legacy Computing" block. The C64 colour palette is
Pepto's. Product names (Commodore, C64 Ultimate, etc.) are trademarks of their owners; this project isn't
affiliated with them.
