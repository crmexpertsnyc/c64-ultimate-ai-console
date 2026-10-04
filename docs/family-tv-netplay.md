# Family profiles, TV mode, netplay and the compatibility log

## 👪 Family profiles
**Top bar → your avatar → Who's playing?** Add up to 12 people, each with a name, an emoji and a colour.
"Child" gives family-friendly recommendations only.

- **Per person:** recommendations (✨ For you), 👍 / 👎, taste history ("What I've learned"), the weekly recap
  and Browser Play saves.
- **Shared:** the library, playlists (game nights are for everyone), guides, details and the compatibility log.
- **How:** the device remembers its player (`localStorage c64.profile`) and sends it with every request as
  `X-C64-Profile`. Where a header can't be sent (image links, `sendBeacon`), it goes as `?profile=`.
  Middleware (`app/profiles.py`) puts it in a context variable, so taste signals, ratings, the recap folder,
  the recommendation cache and save paths follow it. Unknown ids fall back to profile 1.
- **Your existing data:** everything made before profiles belongs to profile 1 ("Player 1"; rename it).
  Profile 1's saves stay in `data/savestates/`; other people's go in `data/savestates/p<id>/`. The database is
  upgraded in place: new columns, and one rating per game *per person*.
- **API:** `GET/POST /api/profiles`, `PATCH/DELETE /api/profiles/{id}` (deleting forgets that person's taste;
  their save files stay on disk). `GET /api/saves` lists this person's games with a save.

## 📺 TV / couch mode
**Top bar → 📺 TV**, or open `/tv`. Full screen, made for the TV and the couch, and driven by a gamepad, a TV
remote or the keyboard: D-pad / arrows move, A / Enter select, B / Esc back.

- **Who's playing?** first, when there is more than one profile. It works with the gamepad too.
- **Rails:**
  - quick actions (🎲 Surprise me, 👪 Switch player, ⏏ Exit)
  - ▶ Continue (your saves)
  - ✨ For you
  - 🎉 Playlists (a playlist opens as its own rail)
  - ★ Favorites
  - 🕘 Recently played
  - 📚 Library
- **A game:** big buttons: **💻 Play here / ▶ Continue here** (full screen, bars only on hover), **📺 Play on
  the C64** (switch the TV to the C64), **👍**, **Back**.
- **In a game from TV mode:** hold **Select + Start** on the gamepad (or Shift+Esc) to return to TV mode.

## 👥 Netplay: play together (host-streamed co-op)
In Browser Play, **👥 Invite** opens a room with a link and QR code. A friend opens it on their own device, types
a name and joins. They see and hear the game and play **player 2**, on the other joystick port, with arrow keys
+ Space, a gamepad or touch controls. Enter, F1–F7, Y/N, 1/2 and Esc are sent as C64 keys. The delay is shown.

- **How:** the host's browser runs the game and streams the emulator's picture (50 fps) and sound to the guest
  over WebRTC. The guest's joystick comes back on a data channel. The console only relays the connection setup
  (`/ws/netplay/{code}`); no game data passes through it. Rooms live in memory and end when the host leaves.
  Up to 3 guests.
- **Why streaming:** EmulatorJS's own netplay (both sides emulating in lockstep) is broken in 4.2.3.
  Streaming needs no sync, and any browser can be the guest, phones included.
- **Where it works:**
  - Best on the same Wi-Fi, or across Tailscale.
  - Away from home without Tailscale, a STUN server helps the browsers find each other (`NETPLAY_STUN`,
    default Google's public STUN; set it empty to turn it off).
  - There is no relay server (TURN), so very strict networks may not connect.
- **Tested:** two separate browsers. The guest received the game at ≈45 fps with sound, 0 frames dropped,
  1 ms delay on the LAN. (In one browser with two tabs, the hidden host tab stops drawing — use two devices.)

## 🐞 Compatibility log
Games that did not work in Browser Play, with the evidence to investigate later. It is linked from the
Library, from Logs & troubleshooting, and on the game page when a game has open reports.

- **Automatically:** the console couldn't prepare the file (won't load), the emulator never started within
  75 s, or the screen froze while loading (the 🛟 rescue). Repeats of the same automatic problem with the same
  file are counted on one entry.
- **⚑ Report** in the game bar: a category (won't load, never starts, hangs, crashes, graphics, sound, controls,
  too slow, other), a note, and a screenshot.
- **Captured with each report:** the file, its SHA-256 and format, the disk, seconds since start, fps, screen
  mode, the analyzer state, the screen text, your last keys, emulator errors, sound state, joystick port, key
  map, mode, controllers and browser.
- **Per entry:**
  - rule-based likely causes: tape image, copy-protected G64, custom fast loader, a multi-disk game waiting
    for disk 2, NTSC on PAL, too slow, joystick port, emulator errors
  - 🔍 Investigate (an AI analysis with next steps)
  - status (open / investigating / fixed / won't fix) and "what fixed it"
  - "✅ reached gameplay since", when the game later worked
  - ⬇ Export CSV
- **API:** `POST /api/issues`, `GET /api/issues`, `PATCH`/`DELETE /api/issues/{id}`,
  `POST /api/issues/{id}/investigate`, `GET /api/issues/export.csv`.

## 🖼 Box art everywhere
Recommendations and playlists show box art for games that aren't in the library yet:
`/api/art/title?name=…` looks up the libretro box art, then the title screen, then the CSDb release's
screenshot. Images are downloaded once and cached.
