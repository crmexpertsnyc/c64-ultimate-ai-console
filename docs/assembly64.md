# Assembly64, Spiffy `server.json` and Home Assembly 64

## Online catalog (Catalog page, and "Play <title>")

The console can use the public Assembly64 catalog (`ASSEMBLY64_URL`, default
`https://hackerswithstyle.se`) directly:

* **Catalog page** — search Games / Music / Demos / Tools, see the source repository, list an entry's
  files, **▶ Play** (download + launch) or **+ Library** (download only).
* **"Play Bruce Lee"** — if the title isn't a strong match in your local library, the console searches
  the catalog and plays the best exact‑title match. Games: Gamebase64 → OneLoad64 → C64.com → CSDB
  (CSDB alone is what `category:games` returns, so curated repositories are queried explicitly); within a
  source, the literal spelling and older ids (usually the original release) win. Music: HVSC; when several
  tunes share a title it asks — say "play Commando by Rob Hubbard" to pick the composer. Other versions
  are offered as clickable suggestions.
* Downloads go to `DATA_DIR/assembly64/<category>-<id>/`, are MD5‑verified against the server's
  `checksum` header, and are indexed into the library (tag `assembly64`), so the next play is local.
  Multi‑disk releases are downloaded together and numbered in filename order when they carry no disk
  marker (e.g. Gamebase `SUMMER1A/1B/1C`), so "Put disk 2 in" works. TAP files and archives are skipped
  (the REST API can't start them).

**Client-Id:** the service only accepts registered client ids — unknown ids get HTTP 464. Spiffy firmware
uses `Spiffy` for this same catalog when no `server.json` overrides it; set `ASSEMBLY64_CLIENT_ID` (or the
Catalog page setting) to an id you are entitled to use.

Verified live against the real catalog: search, entry listing, checksum‑verified download, T64 extraction
and launch on a C64 Ultimate (1.1.0s2).

Commodore firmware 1.1.0 replaced Assembly64 with CommoServe; the Spiffy patch restores Assembly64 and
makes the servers configurable in `/flash/config/server.json`:

```json
{
  "assembly64": [
    {
      "name": "Assembly64", "host": "hackerswithstyle.se", "port": 80, "client-id": "Spiffy",
      "url-search": "/leet/search/aql/0/100?query=", "url-patterns": "/leet/search/aql/presets",
      "url-entries": "/leet/search/entries", "url-download": "/leet/search/bin"
    }
  ]
}
```

## What the console does

* **Reads** the file over FTP (`RETR`, read‑only) — *Assembly64 → Read*.
* **Generates** an entry for your own server and a merged‑file preview, with instructions.
* **Never writes** `server.json` or anything else under `/flash`.

## Running a local server

[Spiffy's Home Assembly 64](https://github.com/spiffycrew/Spiffy_Home_Assembly_64) serves a folder of C64
files with an Assembly64‑compatible API:

```bash
python spiffy_home_assembly64.py -d /path/to/c64/files        # port 8000
```

Then in the console: *Assembly64 → Generate* with this machine's IP and port 8000, copy the merged JSON,
and install it yourself:

1. FTP to the Ultimate and download `/flash/config/server.json` as a backup.
2. Replace it with the merged JSON (or add the entry by hand).
3. Reboot the Ultimate.

## Indexing a Home Assembly server

*Assembly64 → Browse* searches the server (`/leet/search/aql/0/50?query=(name:"…")`), lists an entry's
files (`/leet/search/entries/<id>/<category>`), and **Add to library** downloads a file
(`/leet/search/bin/<id>/<category>/<content id>`, multipart or raw) into `DATA_DIR/assembly64/` and scans
it. The download path follows the Home Assembly 64 README; verify it against your server version.
