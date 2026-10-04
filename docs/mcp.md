# Connecting AI agents (MCP)

The console includes an **MCP server** (Model Context Protocol), so AI assistants such as Claude
Code, Claude Desktop, VS Code (Copilot agent mode), Cursor and others can use your C64: play games,
search the catalog, look at the screen, swap disks, record, and so on.

The easiest way to connect is in the app: **Settings → Connect AI agents (MCP)** shows your URLs and a
copy‑ready setup for each client. This page explains the same thing in full.

---

## 1. Two ways to connect

| | Built‑in HTTP endpoint (recommended) | Stdio launcher |
|---|---|---|
| Address | `http://<console>:8064/mcp` (Streamable HTTP) | `python backend/c64_mcp.py` |
| Needs | the console running | the console running on this PC |
| Use with | Claude Code, VS Code, Cursor, MCP Inspector, OpenWebUI, other machines | Claude Desktop (which only starts local programs) |
| Extra process | no — part of the console | started by the client |

Both expose the **same tools** and both go through the console's own API, so every action is
capability‑checked and appears in **Logs** with source `mcp`.

The endpoint is on by default (`MCP_ENABLED=true`). If the `mcp` Python package is missing the console
still runs, just without `/mcp` (`pip install ".[mcp]"`; the Docker image includes it).

## 2. Security — read this first

The console has no user accounts, so the MCP endpoint protects itself:

* **No token set (default):** only programs **on the same computer** as the console can connect
  (`localhost`, `127.0.0.1`, `::1`). Requests coming from web pages on other sites are refused
  (protection against DNS‑rebinding attacks). You can also allow specific machines by name/IP with
  `MCP_ALLOWED_HOSTS` (e.g. `spark-2d40`) — only do that on a network you trust.
* **Token set:** every request must include `Authorization: Bearer <token>`; then it works from any
  device that can reach the console. Create one in **Settings → Connect AI agents → Create token**
  (or set `MCP_TOKEN` in `.env`). The token is stored like your other secrets: never shown again,
  never logged. Changing or removing it applies immediately.

Use a token whenever the AI client runs on a different machine than the console.

**What AI agents can and cannot do.** Available: device info, play games (library or online), search,
now‑playing, screenshots, disk swaps, reset, key presses / typing / joystick (where the firmware
supports it), Ultimate menu, SID playback, recording, natural‑language commands. Never exposed:
memory read/write, configuration writes, drive ROM loading, firmware, **power off** (refused even via
`c64_command`), going live on Twitch/YouTube, and vision control.

## 3. Client setup

Replace `localhost` with the console's IP or hostname when the client is on another machine, and
add the `Authorization` header when you use a token.

### Claude Code

```bash
# console on this PC, no token
claude mcp add --transport http --scope user c64 http://localhost:8064/mcp

# console elsewhere / with a token
claude mcp add --transport http --scope user c64 http://192.168.1.226:8064/mcp --header "Authorization: Bearer <token>"
```

`--scope user` makes it available in all your projects (`local` = this project only, the default;
`project` = shared via `.mcp.json`). Check with `claude mcp list`, or type `/mcp` inside Claude Code.

### Claude Desktop (on the same PC as the console)

Claude Desktop starts local programs (stdio). Edit `claude_desktop_config.json`:

* Windows: `%APPDATA%\Claude\claude_desktop_config.json`
* macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "c64": {
      "command": "C:\\Users\\<you>\\c64-ai-console\\backend\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\<you>\\c64-ai-console\\backend\\c64_mcp.py"],
      "env": { "MCP_API_BASE": "http://127.0.0.1:8064" }
    }
  }
}
```

(macOS/Linux: `.../backend/.venv/bin/python` and `.../backend/c64_mcp.py`.) Use the launcher script
`c64_mcp.py`, not `python -m app.mcp.server`: Claude Desktop does not honour a working directory, and the
launcher works from anywhere. Restart Claude Desktop afterwards; the tools appear under the tools icon.
The exact paths for your install are shown in Settings.

### Claude Desktop on another computer

Use the `mcp-remote` bridge (needs Node.js on that computer) and a token:

```json
{
  "mcpServers": {
    "c64": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://192.168.1.226:8064/mcp", "--allow-http",
               "--header", "Authorization:${AUTH_HEADER}"],
      "env": { "AUTH_HEADER": "Bearer <token>" }
    }
  }
}
```

`--allow-http` is needed because the console serves plain HTTP on your LAN. The header is passed
through an environment variable to avoid problems with spaces in arguments on Windows.

### Claude.ai (web) and the Claude mobile apps

Custom connectors on claude.ai are called from Anthropic's servers, so they need a **public HTTPS URL**;
a LAN address like `http://192.168.x.x` will not work. If you want this, publish the console through
a tunnel with HTTPS (for example Tailscale Funnel or a Cloudflare Tunnel), **set an MCP token first**,
and add the `https://…/mcp` URL as a custom connector. Think carefully before exposing your console to
the internet.

### VS Code (Copilot agent mode)

`.vscode/mcp.json` in a workspace (or your user MCP configuration):

```json
{
  "servers": {
    "c64": {
      "type": "http",
      "url": "http://localhost:8064/mcp",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

### Cursor

`~/.cursor/mcp.json` (all projects) or `.cursor/mcp.json` (one project):

```json
{
  "mcpServers": {
    "c64": {
      "url": "http://localhost:8064/mcp",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

### Other clients (OpenWebUI, LM Studio, custom agents)

Any client that supports **Streamable HTTP** MCP servers can use `http://<console>:8064/mcp` with an
optional `Authorization: Bearer <token>` header. Clients that only support stdio can use
`c64_mcp.py` (same machine) or `mcp-remote` (other machine) as shown above.

### Testing with MCP Inspector

```bash
npx @modelcontextprotocol/inspector
```

Choose **Streamable HTTP**, enter `http://localhost:8064/mcp`, add the Authorization header if you use
a token, then **Connect → List tools**. Try `c64_device_info` and `c64_screenshot`.

## 4. Tools

| Tool | What it does |
|---|---|
| `c64_device_info` | Connection, product, firmware, capabilities, input mode |
| `c64_now_playing` | Current game, disk n of m, last launch progress |
| `c64_screenshot` | **See the screen** — returns the current picture as a PNG image (not saved) |
| `c64_save_screenshot` | Save a screenshot to the Gallery |
| `c64_play_game` | Play by title (library first, then Assembly64 online) or by library id |
| `c64_search_games` / `c64_list_library` | Search / list the local library (all, favorites, recent) |
| `c64_search_catalog` / `c64_play_catalog` | Search Assembly64 (games, music, demos, tools) and start a result |
| `c64_play_sid` | Play a SID tune by title |
| `c64_mount_disk` / `c64_next_disk` / `c64_previous_disk` | Disk swapping for multi‑disk games |
| `c64_reset` | Reset the C64 |
| `c64_press_key` / `c64_type_text` / `c64_joystick` / `c64_release_all` | Input (typing works at the BASIC prompt on current C64U firmware; joystick in games needs the [joystick bridge](joystick-bridge.md) or firmware with network input; keys in games need firmware with network input) |
| `c64_open_menu` / `c64_read_menu` / `c64_menu_navigate` | Ultimate menu (reading/navigating needs firmware with `menu_screen`) |
| `c64_record` | Start/stop recording to MP4 |
| `c64_command` | Any natural‑language command, same as the command bar |

Example prompts: *"What's on my C64 screen right now?"* · *"Find Bubble Bobble online and start it."* ·
*"Play something by Rob Hubbard."* · *"Put in disk 2."* · *"Record the next minute of gameplay."*

## 5. Troubleshooting

| Symptom | Fix |
|---|---|
| `403 host '…' not allowed` | You're connecting from another machine without a token: create a token (recommended) or add the host to `MCP_ALLOWED_HOSTS`. |
| `401 missing or invalid bearer token` | A token is set; send `Authorization: Bearer <token>` exactly (check for extra spaces/quotes). |
| `404` on `/mcp` | `MCP_ENABLED=false`, or the `mcp` package isn't installed. |
| Claude Desktop shows no tools | Check the JSON paths (double backslashes on Windows), that the console is running, then fully restart Claude Desktop. Its MCP logs are in `%APPDATA%\Claude\logs` / `~/Library/Logs/Claude`. |
| Tools fail with "C64 Ultimate not connected" | The console can't reach the C64 — check the Console page. |
| `mcp-remote` refuses the URL | Add `--allow-http` (plain HTTP on the LAN). |

Every MCP action is listed under **Settings → Advanced → Logs** with source `mcp`.
