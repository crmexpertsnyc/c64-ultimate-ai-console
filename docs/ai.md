# AI providers and vision mode

## Command understanding

```
text → rule parser (deterministic) ──UNKNOWN──▶ LLM → JSON → Intent.model_validate → CommandRouter
```

* Rules always run first; if they match, the LLM is not called.
* The LLM is prompted with the intent list and must answer with one JSON object. Invalid JSON, unknown
  intents, unknown keys or out‑of‑range values are rejected and the command is reported as not understood.
* The LLM can also pick a title from your library for vague requests ("that karate game") — only an exact
  title from the list is accepted.

| `AI_PROVIDER` | Endpoint | Example `AI_BASE_URL` |
|---|---|---|
| `none` | — (default, rules only) | |
| `vllm` | `{base}/chat/completions` | `http://dgx-spark:8000/v1` |
| `ollama` | `{base}/api/chat` with `format: json` | `http://localhost:11434` |
| `openwebui` | `{base}/chat/completions` + API key | `http://host:3000/api` |
| `openai` | `{base}/chat/completions` | any OpenAI‑compatible server |
| `anthropic` | `{base}/v1/messages` + API key | `https://api.anthropic.com` (default) |

Local/private providers are the intended default; the UI marks a provider as *local* when its URL is on a
private network. **Settings → Test provider** lists the server's models and checks that `AI_MODEL` exists.

## Vision mode (experimental)

```
video stream → frame sampler → vision model → VisionAction (validated) → governor → joystick / keys
```

Implemented: frame sampling from the stream adapter, a planner for OpenAI‑compatible and Anthropic vision
models, an action whitelist (joystick directions/fire, RETURN, SPACE, F1/F3/F5/F7) and a governor with
actions‑per‑second, actions‑per‑session and session‑duration limits. Every stop path sends `release_all`.

It is off unless `VISION_ENABLED=true`, and each session must be started explicitly on the Display page
with a goal ("press whatever starts the game"). MCP clients cannot start it. Treat it as a prototype:
quality depends entirely on the vision model.

## Ask mode (questions, with optional Brave web search)

The command bar understands questions as well as commands: "what are the best C64 shoot 'em ups?",
"research the best Epyx games", "tell me about The Last Ninja", "is Summer Games multiplayer?". Click
**▶ Do** to switch it to **💬 Ask** to force question mode.

* The configured AI model answers (`POST /api/ask`, also reachable as intent `ASK`).
* With a **Brave Search API key** (Settings → AI assistant → Brave Search API key, stored as a secret)
  the question is searched on the web first and the model answers from the results, citing them [1] [2].
  Without a key, or if the search fails, it answers from its own knowledge and says so.
* Every game the answer names is matched to your library, else to the Assembly64 catalog, and shown
  with **📺 On my C64 / 💻 In browser** buttons.
* Asking never performs a machine action — playing is always a separate click.

### Games from other sources (itch.io, CSDb, Lemon64)

New and homebrew games are often not in the Assembly64 catalog yet. When an Ask answer names a game that
is in neither your library nor the catalog — or a Catalog search finds nothing (**🔎 Search itch.io, CSDb &
Lemon64**) — the console searches those sites (needs the Brave key) and shows what each offers:

* **CSDb release** — fetched automatically (Assembly64 mirrors CSDb releases under the same id; otherwise
  **⬇ Add from CSDb** downloads the release file from csdb.dk). Then 📺 On my C64 / 💻 In browser.
* **itch.io** — **▶ Play on itch.io ↗** (the game's browser build, on itch) or **🛒 Get it on itch.io ↗**
  (buy / name your price / download). itch.io pages cannot be embedded, and buying there supports the developer.
* **Lemon64** — link to the game page.
* **⬆ Add downloaded file** / Library → **⬆ Add game file** (or drag & drop): .prg .d64 .crt .t64 .g64 .tap
  .zip … go to `DATA_DIR/imports/` and play like any other title. The same file is never added twice.

Automatic downloads are only ever made from CSDb; everything else is a link you open.


## Online archives (no key needed)

Catalog → a search that finds nothing in Assembly64 searches the online archives automatically. When there
are catalog hits, **🌐 Search online archives** runs the same search. Ask also looks up every named game
that isn't in the library or catalog. Implementation: `backend/app/services/sources.py`,
`GET /api/sources/search?q=`, `POST /api/library/import-source {source, id, title}`.

| Source | How | Play |
|---|---|---|
| Internet Archive — C64 Software Library + Ultimate Tape Archive | JSON search | ⬇ Add & play (disk, tape, cartridge; several disks → one title) |
| C64.com | HTML search | ⬇ Add & play (zip → disk image) |
| Games That Weren't 64 | WordPress REST search | ⬇ Add & play where the entry has a file, otherwise its page |
| GameBase64 | link (behind a Cloudflare check; no downloads — details and credits) | — |
| CSDb | link to its search (its robots.txt disallows automated search); downloads by release id as before | — |
| Lemon64 | link (Cloudflare) — a site web search | — |

The Ultimate Tape Archive's own site no longer resolves, so it is used through its Internet Archive mirror.
Requests go only to fixed endpoints of these sites, built from a search text or a validated id — never
from a URL supplied by a caller or the AI. Downloads must stay on the archive's hosts after redirects, and
are size-limited. Files are only kept when they contain a C64 file. Nothing is fetched until you search or
press ⬇ Add & play. Tape (.tap) titles play in the browser emulator; the real C64's REST API can't start
tapes.

## Recommendations ("✨ For you")

The Console shows games picked for you. It learns from what you do, and everything stays on this console:

| Signal | Weight |
|---|---|
| 👍 on a game (game page, Browser Play bar, a pick) | strong |
| ★ Favorite | medium |
| Playing a game (on the C64 or in the browser) | medium |
| Minutes played in the browser (tab visible, 30 s minimum) | up to strong |
| Searches and questions (command bar, catalog, archives, Ask) | light |
| 👎 | removes the game and steers away from it |

Signals fade over time (half-life 45 days). `backend/app/services/taste.py` builds a **taste profile** from
them: liked games with the reasons, disliked games, recent searches, and favourite genres, publishers and
eras (from library metadata).

`backend/app/services/recommend.py` makes the picks:
- **With an AI model:** the profile and your unplayed library titles go to the model, with a Brave web
  search for "games like …" when a key is set. It returns 12 picks — mostly close matches, some 💎 hidden
  gems, one or two 🎲 "something different" — each with a reason and "because you liked …". Liked,
  disliked and already-played games are filtered out.
- **New player:** a varied starter list of classics across genres.
- **Without AI:** unplayed library games that share a genre, publisher or era with games you like.

Every pick is resolved like an Ask answer (library → Assembly64 catalog → online archives), so it plays
straight away. Picks are cached until the profile changes; they refresh by themselves at most every
30 minutes, and **↻** refreshes on demand. **🧠 What I've learned** shows the profile, lets you remove any
game, or forget everything.

API: `GET /api/recommendations`, `POST /api/recommendations/refresh`, `POST /api/taste/rate`,
`GET /api/taste/rating`, `POST /api/taste/event`, `GET /api/taste/profile`, `DELETE /api/taste/game`,
`DELETE /api/taste`. Setting: `RECOMMEND_TIMEOUT` (default 180 s — local models need longer for a
12-game answer than for a command).

## ✨ Fill in details
Library → **✨ Fill in details** (whole library, runs in the background), or **✨ Fill in details** on a game page.
The console web-searches each game (Brave) and the AI returns year, publisher, genre, players, joystick port, a
one-line description and style tags from a fixed list (co-op, versus, party, relaxing, great music, hard,
family-friendly, keyboard needed…). **Only empty fields are filled**; anything you set is kept. Description,
tags and sources are stored in the game's `details`. Library search matches genre, tags and description ("co-op",
"great music"), and genres feed the recommendations.
`POST /api/games/{id}/details`, `POST /api/library/details` (+ `GET` for progress) — `app/services/enrich.py`.

## 💡 Stuck? (Browser Play)
Spoiler-free help for what's on screen. It reads the C64's screen memory, plus your question and the game's
guide. You get **💡 a nudge**, then **🧭 more help** (with a walkthrough web search), then **🔓 the solution**
only when you ask. `POST /api/games/{id}/hint` — `app/services/coach.py`.

## 🧭 Text adventure co-pilot (Browser Play)
Appears for adventure games: by genre or style tag, or a text screen read by the C64's own keyboard routine. It
records the adventure's text as it appears, works out the room, exits, inventory and current goal, keeps a map
(saved per game in this browser), and suggests 3–5 commands. Click one to type it on the C64 with RETURN.
Commands are checked (capital words only) before they can be typed.
`POST /api/games/{id}/copilot`. Tested on *Mission Impossible* (Scott Adams): it found "Briefing room · exits
W", suggested INVENTORY / GET TAPE / GO WEST…, and typing INVENTORY showed the bomb.

## 🛟 When a game hangs (Browser Play)
If the screen stays frozen for 45 s after start or after your last key press, nothing has been pressed since,
and nothing on screen asks for a key, a banner offers other versions that are likely to start: another copy in
the library (EasyFlash cartridge first), then one-file releases from the catalog (EasyFlash / OneLoad64 — no
custom disk loader). It also offers "Press Space / Fire" (for quiet title screens) and "Keep waiting". The hang
is remembered on the game, so next time the warning appears up front.
`GET /api/games/{id}/alternatives`, `POST /api/games/{id}/hang` — `app/services/rescue.py`. Tested on
*The Last Ninja* (Real Stuff crack): it offered *The Last Ninja (EasyFlash)*.

## 🎉 Game nights & playlists
**Playlists** in the menu: describe the occasion ("4-player party pack for Friday night", "30-minute lunch
break"). The AI builds an ordered list of real C64 games — library and taste first — with players, time and a
note (turns or simultaneous, controllers) for each. A player count in the request is enforced: games that can't
take that many players are dropped. Every game plays straight away; tick games off or remove them.
`/api/playlists` — `app/services/playlists.py`.

## 📊 Your week
On the Console: play time in the browser, games started, days active, new games tried and most played (last
7 days, from the taste signals). The AI adds a headline, a two-sentence summary and three challenges for games
you played ("Finish the first mission in Laser Squad"), which you can tick off. Cached per week; ↻ recounts.
`/api/recap` — `app/services/recap.py`.

**Honest limits:** these features use whatever model you configure. A small or local model can be wrong on
facts: players, challenge details, hints. The console enforces what it can check (the tag and genre lists, the
player count, command format), but it can't verify game trivia.

## 🤖 AI on the real C64

The **C64 Screen** page now has an *AI assist* bar whenever the C64 Ultimate is connected. Every 2 seconds the console reads the
real machine's screen from its memory (`GET /api/device/screen/sample`, built on the Ultimate's read-only `machine:readmem` —
nothing is ever written). It finds the screen the way the VIC-II does ($DD00/$D018, text vs bitmap from $D011) and falls back to
$0288 if the I/O area isn't visible. It then runs the same startup analyzer as Browser Play:

- **▶ Next: X — press it.** The console presses the key itself when it can:
  - joystick directions and fire go through the joystick bridge;
  - RUN, RETURN, letters, digits, SPACE and F-keys are typed into the KERNAL keyboard buffer.
  - Keys this firmware can't send over the network (RUN/STOP, RESTORE, C=) produce a message telling you which key to press
    on the C64's keyboard.
- **💡 Stuck?** gives spoiler-free hints about what's on screen.
- **🧭 Co-pilot** types commands into text adventures on the real C64.

## 🏆 Achievements and high scores

Score, high score, round/level, lives and time are read from the game's **own screen text**, both in Browser Play and on the
real C64. Nobody types a score in.

- A value counts only when two consecutive readings agree. Absurd jumps are ignored, and nothing counts while the analyzer
  sees an attract/demo screen.
- Built-in achievements apply to every game: *Into the game*, *Hour of power*, *Regular*.
- Once a score or round has been seen, the AI drafts game-specific achievements, using only the metrics the game actually
  shows. Browser Play does this automatically the first time; the game page has a 🤖 button.
- Unlocks and the high-score table are kept per family profile.
  - 📺 C64 scores are read by the console itself (verified).
  - 💻 browser scores come from the emulator in your browser.

API: `POST /api/device/progress`, `POST /api/games/{id}/progress`, `GET /api/games/{id}/achievements`,
`POST /api/games/{id}/achievements/generate`, `GET /api/achievements/recent`.

Games that draw their score with a custom font or as graphics (bitmap) have no readable digits, so they only get the
built-in achievements.
