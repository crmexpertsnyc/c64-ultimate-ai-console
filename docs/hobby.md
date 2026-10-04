# Hobby features

Seven features under **Hobby** in the menu. Each one is a self-contained plug-in (`backend/app/features.py`):
- `app/models/<name>.py`
- `app/services/<name>.py` with `attach(container)`
- `app/api/<name>_api.py`

Background checks follow the `NEWS_MONITOR` setting (Settings → Hardware shop & eBay).

## 🛒 Hardware (`/shop`): link-out only
- **Catalog:** 39 verified products from 15 sellers, covering power supplies and over-voltage protection, REU/EasyFlash
  cartridges, SD storage, joysticks and wireless pads, video, SID/PLA/RAM replacements, diagnostics, cases and C64 Ultimate
  accessories.
  - Built-in file: `app/data/shop_catalog.json`.
  - Your website can publish its own copy: set `SHOP_CATALOG_URL`. The catalog must be https; it is validated and refreshed
    daily.
- **Affiliate programs (checked 2026-10-03):**
  - None of the C64 specialist sellers runs one. That covers Commodore, Individual Computers, Protovision, Retro
    Innovations, Lotharek, Tindie, Ultimate 64, Retro Rewind and Poly.Play. Commodore's "Refer a friend" is for customers only.
  - Only **eBay Partner Network** and **Amazon Associates** are open to third-party sites. Every catalog link is therefore
    marked non-affiliate, and the disclosure line is shown anyway.
- **Used & rare:**
  - With eBay developer keys (`EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET`), live listings come from the Browse API. An EPN
    campaign id (`EBAY_CAMPAIGN_ID`) returns affiliate links (`itemAffiliateWebUrl`).
  - Without keys, the page shows eBay search links (EPN-tagged when a campaign id is set).
  - Production Browse API access goes through an EPN application.
- **💰 Price watches:** a listing at or under your price becomes a "deal" item, counted in the 🆕 badge.
- **Suggestions that know your setup:**
  - Game page and compatibility reports: the game needs an REU or EasyFlash, is a cartridge image, uses a mouse or paddles,
    or is for two players.
  - Controller page: keyboard as the joystick, so it suggests wireless joysticks.
  - Repair results: the parts to fix it.
- **🤖 Starter kit:** the AI picks only from catalog ids.

## 🔧 Repair (`/repair`)
- **Machines:** breadbin, C64C, SX-64, C64 Ultimate and 1541.
- **Symptoms and causes:** `app/data/repair_kb.json` holds each symptom with its causes (likelihood, why, checks, part tags).
- **AI ranking:** the AI ranks only these known causes and adds follow-up questions. Without an AI model, keyword matching
  is used.
- **Safety:** the original-PSU warning is always shown for original machines.
- **Also shown:** service manuals and guides (zimmers.net, archive.org, Ray Carlsen), repair videos from the monitored
  channels with a YouTube search link, and parts from the catalog.

## 🎵 Jukebox (`/jukebox`)
- **Source:** HVSC through Assembly64. Search by title or composer (`group:"Hubbard"`).
- **Playback:** plays on the real C64 Ultimate through `runners:sidplay`; play history is kept.
- **🤖 Stations:** the AI picks tunes, resolved against Assembly64 ids only.
- **Stop** resets the C64; nothing else is sent to the device.

## 📦 Collection (`/collection`)
- **Logging:** boxed games, cartridges, disks, tapes and hardware, with condition, boxed/complete, quantity, serial,
  storage place and price paid. Games are matched to the library for box art.
- **💲 Value:** the median of current eBay asking prices. eBay's sold-price API (Marketplace Insights) is limited to
  approved partners, so a "sold listings" link is offered; you can also type your own value.
- **⭐ Wishlist:** an item with a target price automatically becomes a 💰 price watch.
- **Exports:** CSV, and a printable insurance report (`/api/collection/report`).

## 📚 Magazines (`/magazines`)
- **Series:** Zzap!64, Compute!'s Gazette, Commodore User, Your Commodore, RUN, Commodore Horizons and Commodore Format,
  read from the Internet Archive scans in the app.
- **Make searchable:** downloads each issue's OCR text into a local full-text index (`data/magazines.db`, SQLite FTS5).
- **🤖 Ask the magazines:** answers only from the matching excerpts, with citations.
- **Game page:** reviews (score and verdict) come from the index.

## 📅 Events (`/events`): all retro events, home country first
- **Scope:** every kind of retro event, not just Commodore. That covers retro gaming expos, vintage computer festivals,
  conferences, conventions, arcade and pinball shows, swap meets, user groups and demoparties. A filter switches between
  *All retro*, *Commodore & scene* and *Other retro*.
- **Filters:**
  - dates: this month, the next 30 days / 3 / 6 / 12 months, or a custom range
  - country (your home country first), then state or province
  - type and keyword
- **⭐ Home country (US by default):** set in Settings → Sources & updates (`EVENTS_HOME_COUNTRY`). Its events get their
  own section at the top, a blue highlight and the state badge; "⭐ … first" can be switched off.
- **Places are tidied:** "USA" and "U.S." become United States, and "Las Vegas, NV" or "Portland, Oregon" give the state.
- **Sources** (all on the scheduler below):

  | Source | What | How often |
  |---|---|---|
  | CSDb upcoming events | Commodore scene parties and meetings (+ website, city) | daily |
  | demoparty.net calendar (ICS) | every demoparty worldwide | daily |
  | Official sites of about 25 recurring shows (`crawlers.EVENT_SITES`) | VCF East/West/Southeast/Midwest/Southwest/SoCal/PNW, KansasFest, CoCoFEST!, AmiWest, PaCommEx, Portland Retro Gaming Expo, Midwest Gaming Classic, Retro World Expo, Southern-Fried, TooManyGames, LI Retro, Classic Game Fest, Texas Pinball Festival, Pinball Expo, California Extreme, Free Play Florida, World of Commodore, VCF Europa, Classic Computing | weekly |
  | 🤖 Web research | expos, conferences, fairs and meetups (US-heavy queries, then worldwide) | weekly |
  | You | your own events | — |

- **How dates are read from official sites:**
  - Order of trust: the page's schema.org Event data, then countdown scripts, then written dates ("October 9-11, 2026",
    "21 to 23 August 2027").
  - Only dates near the show's usual month count, so a swap meet in the sidebar isn't mistaken for the festival.
  - When nothing is written plainly, the AI can read the dates from the page text. The model only returns dates; the
    page itself is always the one we fetched.
- **Web research is checked twice:** links must come from the search results, and an event is dropped when its cited page
  is about a different year (a 2026 article never becomes a 2027 date).
- **Duplicates:** when two sources list the same show, the better one is kept. The order is yours, then official site,
  then CSDb, then demoparty.net, then web.

## 🔄 Sources & updates (Settings)
One scheduler keeps every dynamic source fresh, one job at a time:

| Job | Default |
|---|---|
| News, new releases & videos: CSDb, itch.io (C64 tag, Psytronik, Protovision), Indie Retro News, The Oasis BBS, Commodore, FREEZE64, Protovision, TPUG, Individual Computers, Reddit r/c64, VCF, demoparty.net news, 25 YouTube channels | 30 min |
| Popularity of new releases | 6 h |
| Events: CSDb, demoparty.net | daily |
| Events: official sites, 🤖 web research | weekly |
| Firmware news | daily |
| Magazine archive (new scans; new issues of searchable magazines are indexed) | weekly |
| Hardware catalog and product photos | daily |
| New hardware from the makers: Commodore store, Protovision, Fusion Retro (ZZAP! issues), VideoGamePerfection, Retro Innovations | daily |
| 💰 eBay price watches | 6 h |

- **Per job:** you can change the interval (15 min to weekly), switch it off, or press "Check now".
- **Restarts:** the last result, time and error of each job are kept, so a restart doesn't re-crawl everything.
- **New hardware:** the first look at a shop only records what it already sells, so only products added after that are
  announced as new.
- **Sources that block automated access:** Retro Innovations (sometimes), Midwest Gaming Classic, Classic Game Fest,
  Lemon64, forum64, Meetup and Eventbrite. The last two forbid scraping, so they aren't used at all.

## 🧩 Firmware news (notification only)
- **Sources:** Commodore's download center for the C64 Ultimate, ultimate64.com for Ultimate 64 / II+. Checked daily
  against the device's version.
- **New version:** a banner on Console and Settings, plus one What's new item linking to the release notes and the download
  page.
- **Never:** downloads firmware, sends anything to the device, or offers an update button.
