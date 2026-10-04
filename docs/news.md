# 🆕 What's new: C64 news, new releases and videos

The console checks these feeds every 30 minutes (Settings: `NEWS_MONITOR`, on by default):

| Source | What | In the app |
|---|---|---|
| CSDb latest releases | New games, cracks, demos, intros, music and graphics from the scene | ▶ 💻 play in the browser or 📺 on the C64 with one click. The file is downloaded from CSDb into the library; only files hosted on CSDb can be added this way. |
| itch.io (tag: Commodore 64) | Homebrew games | ▶ On itch.io ↗: play, buy or support the developer there |
| Indie Retro News (C64 label) | News articles | Summary, plus a link to the article |
| YouTube: 27 channels (list in `app/services/news.py` → `FEEDS`). C64-only: Protovision, RetroGamerNation, FREEZE64, The Highlander C64 Gaming, Retro C64 Gaming, C64 Television, Commodore-64 longplays, Transmission64 Demoparty, EverythingSid, Official Commodore. Filtered: Retro Recipes, 8-Bit Show And Tell, Shallan, Chicken 64, Commodore Realm, Linus Åkesson, The 8-Bit Guy, Jan Beta, ChinnyVision, 20th Century Gaming, Adrian's Digital Basement, MonroeWorld, Noel's Retro Lab, The Retro Hour | Videos (kept for a year) | 🎬 Watched in the app with YouTube's privacy-enhanced player. Channels that aren't only about the C64 are filtered to videos that mention it. Shorts are skipped. |

- **New-item alerts:** new items appear as a 🆕 badge on **What's new** in the menu and as a short pop-up message.
- **🤖 This week in C64:** the AI reads the last 7 days of items and picks the ones worth a look.

## 🔥 Most popular and 📈 Trending

Each source's own numbers are used:

- **CSDb:** downloads, comments, votes and the 0–10 user rating, read from the release page.
  - CSDb shows the average only after 8 votes, but the vote count is visible before then.
  - The rating is weighted by how many votes it has, so 3 votes of 10 don't beat 100 votes of 9.5.
- **itch.io:** number of ratings and the star average.
- **YouTube:** views and likes (included in the channel feed).

Because downloads and views aren't on the same scale, each item gets a **percentile within its own source**. "🔥 Top 5%"
means it beats 95% of recent releases on that site.

**Trending** uses the same numbers divided by age, so a day-old release with 300 downloads beats a month-old one with 500.

Release pages are re-read every 6 hours for the first two weeks and daily until 60 days old. The console reads one page at a
time, about a second apart. Items older than 120 days are removed, except releases added to the library.

API:
- `GET /api/news?kind=release|news|video&category=&q=&sort=newest|popular|trending`
- `POST /api/news/refresh`
- `GET /api/news/new?since=`
- `GET|POST /api/news/digest`
- `POST /api/news/{id}/add` → `{gameId}`
