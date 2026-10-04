# Browser Play input: keys, startup detection and game profiles

Browser Play runs VICE (x64sc) in EmulatorJS on your device. This page describes how keyboard, gamepad and
touch input reach the emulated C64, and how the console works out what a game wants in order to start.

Files: `frontend/public/emulator/play.html` (the player, input delivery, screen reading),
`frontend/src/services/autoInput.ts` (Type/Play and gameplay detection),
`frontend/src/services/startAnalyzer.ts` (startup analyzer and learning),
`frontend/src/pages/EmulatorPage.tsx` (UI), `backend/app/services/input_profiles.py` (profiles).

## Keys

| C64 | PC keyboard | Gamepad |
|---|---|---|
| Joystick | Arrow keys (Play, and on start screens in Auto) | D-pad / stick |
| Fire | Space (Play) | A, B or Y |
| **RUN/STOP** | **Ctrl+R** · on-screen button | — |
| RETURN | Enter | Start |
| Space bar | Space (Type, and on screens that ask for SPACE) | X |
| C= | Ctrl · on-screen button | right stick click |
| RESTORE | Page Up · on-screen button | left stick click |
| F1 / F3 / F5 / F7 | same keys | shoulders / triggers |
| Swap joystick port | Ctrl+Alt+P | — |
| Type ⇄ Play | Ctrl+Alt+G | — |
| Leave the game | Shift+Esc · ← Back | — |

**Plain Esc no longer sends RUN/STOP.** Esc is the browser's own key (it leaves full screen and closes
browser popups, and no page can stop that in full screen), so a game that needs RUN/STOP to start would
fight with the browser. Plain Esc now stays with the browser; the first time you press it the console says
where RUN/STOP went. The old mapping is still available: **⌨ Keys → "Esc = RUN/STOP too"**.

### Why Ctrl+R

The shortcut had to be one key combination, easy to remember, and free on every browser and OS:

| Candidate | Problem | Verdict |
|---|---|---|
| Esc | Leaves full screen / pointer lock in every browser; cannot be blocked in full screen | not for RUN/STOP |
| Ctrl+Esc | Windows: opens the Start menu (OS level, cannot be blocked) | rejected |
| Alt+Esc | Windows: switches windows (OS level) | rejected |
| Ctrl+Backspace | Free, but not memorable ("delete word") | rejected |
| **Ctrl+R** | Reload in Chrome / Edge / Firefox on Windows & Linux — but pages **can** cancel it while they have focus, and the console does (both the game and the page around it). macOS reloads with Cmd+R, so Ctrl+R is free there. "R" = RUN/STOP. | **chosen** |

When Ctrl+R is pressed the C= key (Ctrl) is released first, so the C64 sees RUN/STOP alone.

### Other browser / OS shortcuts

| Key | Browser / OS default | Handling while the game has focus |
|---|---|---|
| F1 / F3 / F5 / F7 | help / find / reload / caret browsing | blocked, sent to the C64 |
| Tab, Backspace | focus / (old) back | blocked, sent to the C64 |
| Space, arrows, Page Up/Down, Home, End | page scrolling | blocked |
| Ctrl+R, Ctrl+Alt+G, Ctrl+Alt+P | reload / (AltGr characters on some layouts) | blocked, used by the console |
| Ctrl+W, Ctrl+T, Ctrl+N, Ctrl+Tab | close / new tab / new window / switch tab | **cannot be blocked** by any page. Since Ctrl is the C= key, use the on-screen C= button for C= + W/T/N. The page asks before closing while a game runs, and saves automatically when hidden. |
| Esc | leave full screen, close popups | left to the browser (see above) |
| Shift+Esc | leaves the game (Chrome on Windows may also open its Task Manager window) | used by the console |
| F11, F12 | full screen, developer tools | not used; left to the browser |
| Cmd shortcuts (macOS) | all | not used; left to the browser |

You can never get stuck: Esc and every unblockable browser shortcut still work, and Shift+Esc or ← Back
leaves the game.

## Modes

- **Auto** (default). Before the game runs, the startup analyzer picks the keys per screen: arrows = joystick
  on title and menu screens, cursor keys at the BASIC prompt; Space = the space bar where the screen talks
  about SPACE (and on trainer / option screens), fire where it asks for fire, and on screens that don't
  say — the space bar followed by a short fire press. (Not both at once: joystick 2 fire shares the CIA
  line the keyboard scan uses, so holding fire hides the key from the C64.) Once the game is running
  (the joystick is being moved and the screen is moving — see below) Auto switches to Play. A new start
  screen (a clear "press … to start", a trainer, BASIC) switches back.
- **Play**: arrows = joystick, Space = fire; all other keys still type (Y/N, Enter, F-keys, letters).
- **Type**: the whole keyboard is the C64 keyboard (arrows = cursor keys).

Ctrl+Alt+G switches between Type and Play; clicking Auto returns to automatic.

Controllers: the pad you press first becomes player 1 (port 2). Keyboard players are treated the same
as controller players: gameplay detection counts joystick *movement* (arrow keys used as the joystick,
stick or d-pad) — pressing fire alone is how title screens are skipped, not a sign of playing.

## Startup analyzer

The player reads the C64's memory directly — no OCR. Every 0.7 s it takes a VICE snapshot (about 2–3 ms)
and finds the RAM, the VIC-II registers and CIA 2 in it. From these:

- **What is on screen**: the screen address comes from the VIC bank ($DD00) and $D018. Each of the
  25 rows is decoded as text three ways (ROM upper case, ROM lower case, and "ASCII − 32", common in game
  fonts); the reading with real words wins. Bitmap mode ($D011 bit 5) = a picture; display off = blank.
- **Scrolling text** is stitched together from successive views, so a cracktro's "…PRESS SPACE…" is
  caught even though it is never on screen at once.
- **BASIC**: the banner and the last line tell loading (`SEARCHING`, `LOADING`) from a program that
  loaded but stopped at `READY.` (then the suggestion is to type RUN).
- **Joystick port**: the program is scanned for instructions that read $DC00 (joystick port 2) and
  $DC01 (port 1 / keyboard rows). A program that only ever reads one of them can only use that port.
- **KERNAL keyboard**: the IRQ vector shows whether the C64's own keyboard routine is active.
- **Speed**: running far above 50 fps = warp while loading.

From this the analyzer decides the **state** — loading, BASIC, intro, title, trainer, menu, gameplay — and the
**next action**:

| On screen | Action | Pressed automatically? |
|---|---|---|
| "… RUN/STOP TO START", "PRESS FIRE TO PLAY", "HIT F1 TO BEGIN", "PRESS ANY KEY" | that key | yes (when Auto start is on) |
| "PRESS FIRE FOR &lt;this game's name&gt;" | that key | yes |
| "PRESS SPACE" (no "to start") | Space | no — shown as a button |
| "PRESS 1 OR 2 TO PLAY" | 1 | no — how many players is your choice |
| Y/N options, trainer words | Y / N hints | no |
| READY. after LOADING | type RUN | yes |
| a screen where a key worked before | that key | yes |

A clear "press … to start" or Y/N screen overrides the gameplay detection (a moving title screen is not
gameplay).

### What you see

- A **context help line** replaces the old fixed text, e.g. on Bubble Bobble's docs screen:
  `Bubble Bobble — Space = read · Arrows = joystick · Space = space bar · Ctrl+R = RUN/STOP …`
  and a button **▶ Start: press Ctrl+R (RUN/STOP)** (click it to press the key).
- In the game: `Bubble Bobble — Arrows = move · Space = fire · 🎮 controller · Joystick port 2 · …`
- If the port reading disagrees with the port in use: **🕹 Joystick port 1 appears to be needed — switch
  (Ctrl+Alt+P)**. A port is switched automatically only when the reading is sure (the screen names the port,
  or a learned / built-in profile).

### Auto start (setting)

⌨ Keys → **"Automatically handle known game startup screens"** (off by default). When on, the start key is
pressed for you when the screen clearly asks for it (see the table) or when the same key worked on this
same screen before — once per screen, 1.5 s after it appears, never while you are typing, while the
"Continue where you left off?" choice is open, or after resuming a save.

## Game profiles and learning

`GET /api/games/{id}/input-profile` (also included in `/api/games/{id}/emulator`):

```json
{"game": "Bubble Bobble", "platform": "C64", "sha256": "6c690c2d…",
 "startupSequence": [{"key": "FIRE+SPACE", "when": "PROUDLY PRESENTS / 1987 BY FIREBIRD"},
                     {"key": "RUN/STOP", "when": "SPACE TO READ OR RUN/STOP TO START"},
                     {"key": "FIRE+SPACE", "when": "(picture)"}],
 "startupSource": "learned", "joystickPort": 2, "joystickPortSource": "library", "controls": {}}
```

Layers, most reliable first: **learned** → **built-in** (`backend/app/data/input_profiles.json`) →
the **guide** (📖 How to play) → library metadata. Runtime screen reading always comes first.

**Learning**: when you press a startup key (fire, Space, RUN/STOP, RETURN, F-keys, Y/N, digits — not
joystick moves) and the screen changes to a different screen within 4 s, that is remembered as "on this
screen, this key". When the game is reached, the sequence is saved on the console. Each step is matched to
its screen (not to a time), so a slower or faster load doesn't matter.

Learned profiles are tied to the **SHA-256 of the booted file** (computed once, cached with the media). Another
release of the same game — a different crack or trainer, with different intro screens — does not inherit
them; built-in profiles matched only by title never carry start keys, only port and controls.
⌨ Keys → "Forget the learned start keys" removes them (`DELETE …/input-profile/learned`).

## Diagnostics

⌨ Keys → "Show input diagnostics" (or `?debug` in the URL) shows the input detection plus the **startup
analyzer**: profile layers and file hash, state, active key map, start action with confidence, source and
reason, port guess and reason, screen mode and address, the screen signature, readable text, the stitched
scroller, joystick-register reads in the program, recent inputs and what was learned this session.

## Verified (headless Chrome, real key events)

- **Bubble Bobble** (Remember #161 crack):
  - Space skipped the cracktro.
  - The docs screen showed **▶ Start: press Ctrl+R (RUN/STOP)**, and Space stayed the space bar ("Space = read").
  - Ctrl+R started the game without reloading the page.
  - Space/fire got past the title picture.
  - The trainer screen was detected: Y/N, Space = space bar, "▶ Continue: press Space".
  - "PRESS FIRE FOR BUBBLE BOBBLE" was read from the game's own font.
  - "PRESS 1 OR 2 TO PLAY" is offered as a button, never pressed automatically.
- **Learning and Auto start**: the learned sequence was saved against the file hash. With Auto start on, a
  cold boot went from the intro to the game's title with no key presses. Auto start waits while the
  "Continue where you left off?" choice is open.
- **Library sweep** (45 s per game, no input):
  - Mission Impossible (Scott Adams text adventure) is detected as a question screen: Y/N hints, typing keys.
  - Title pictures (Summer Games, Basketball, Baseball) and intros (Bruce Lee, Winter Games, Last Ninja EF)
    get the generic keys: arrows = joystick, Space = space bar then fire, Ctrl+R = RUN/STOP.
- **BASIC loading** screens switch to typing keys (cursor keys, space bar).
- **Not yet tested by hand**: typing a program at a plain READY. prompt; a machine with no gamepad
  connected (the test machine has pads plugged in, so keyboard-only was only exercised with pads present);
  a game that reads only joystick port 1.

The analyzer is plain TypeScript with no browser dependencies, so its rules can be tested in Node.
