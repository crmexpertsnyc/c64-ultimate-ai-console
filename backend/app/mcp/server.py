"""Optional MCP server exposing SAFE C64 actions to LLM agents.

It is a thin client of the console's own developer API (so there is one owner of the
device connection, every action is audited with source=mcp, and all capability checks
apply). Deliberately NOT exposed: memory read/write, configuration writes, drive ROM
loading, power off, vision control, raw REST passthrough.

Run (stdio, for Claude Desktop / Claude Code / other MCP clients):
    python -m app.mcp.server
Streamable HTTP instead:
    python -m app.mcp.server --http --port 8065
Point at a non-default console with MCP_API_BASE=http://host:8064
"""

from __future__ import annotations

import argparse
import os
from typing import Any, Literal

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver import Image

API_BASE = os.environ.get("MCP_API_BASE", "http://127.0.0.1:8064").rstrip("/")
HEADERS = {"X-C64-Source": "mcp"}

mcp = MCPServer(
    name="c64-ultimate",
    instructions="Control a Commodore 64 Ultimate through safe, capability-checked actions. "
                 "Use c64_screenshot to see the screen, c64_play_game or c64_command to start games. "
                 "Check c64_device_info first. Joystick/keyboard injection may be unavailable on older firmware.",
)


async def _call(method: str, path: str, json: Any = None, params: dict[str, Any] | None = None) -> Any:
    async with httpx.AsyncClient(base_url=API_BASE, timeout=120, headers=HEADERS) as client:
        r = await client.request(method, path, json=json, params=params)
    try:
        data = r.json()
    except ValueError:
        data = {"detail": r.text[:500]}
    if r.status_code >= 400:
        detail = data.get("detail") if isinstance(data, dict) else data
        raise RuntimeError(f"{r.status_code}: {detail}")
    return data


@mcp.tool(description="Connection status, product, firmware, capabilities and input mode of the C64 Ultimate.")
async def c64_device_info() -> dict[str, Any]:
    st = await _call("GET", "/api/device")
    caps = st.get("capabilities", {})
    return {"connected": st.get("connected"), "info": st.get("info"), "apiVersion": st.get("apiVersion"),
            "inputMode": st.get("inputMode"), "drives": st.get("drives"), "session": st.get("session"),
            "capabilities": caps.get("capabilities"), "usable": caps.get("usable")}


@mcp.tool(description="Search the local game/music library by title or publisher.")
async def c64_search_games(query: str, limit: int = 20) -> list[dict[str, Any]]:
    data = await _call("GET", "/api/library", params={"q": query, "limit": max(1, min(limit, 50))})
    return [{k: g.get(k) for k in ("id", "title", "publisher", "year", "format", "numDisks", "category")}
            for g in data["items"]]


@mcp.tool(description="Play a game by library id, or by title (fuzzy matched). Returns the launch job.")
async def c64_play_game(title: str | None = None, game_id: int | None = None) -> dict[str, Any]:
    if game_id is not None:
        return await _call("POST", f"/api/games/{int(game_id)}/play", json={})
    if not title:
        raise ValueError("title or game_id required")
    return await _call("POST", "/api/intent", json={"intent": {"intent": "PLAY_GAME", "game": title}})


@mcp.tool(description="Reset the C64 (all injected inputs are released first).")
async def c64_reset() -> dict[str, Any]:
    return await _call("POST", "/api/device/reset")


@mcp.tool(description="Insert disk N of the currently running multi-disk game into drive A.")
async def c64_mount_disk(disk: int) -> dict[str, Any]:
    return await _call("POST", f"/api/session/disk/{int(disk)}")


@mcp.tool(description="Insert the next disk of the current multi-disk game.")
async def c64_next_disk() -> dict[str, Any]:
    return await _call("POST", "/api/session/next-disk")


@mcp.tool(description="Tap, press or release a C64 key (return, space, run_stop, f1, cursor_down, a, 1, ...).")
async def c64_press_key(key: str, transition: Literal["tap", "press", "release"] = "tap") -> dict[str, Any]:
    return await _call("POST", "/api/device/input", json={"key": key, "transition": transition})


@mcp.tool(description="Type text on the C64 keyboard (max 200 chars); optionally press RETURN afterwards.")
async def c64_type_text(text: str, press_return: bool = False) -> dict[str, Any]:
    if len(text) > 200:
        raise ValueError("text too long (max 200)")
    return await _call("POST", "/api/device/type", json={"text": text, "press_return": press_return})


@mcp.tool(description="Joystick input. inputs: up/down/left/right/fire/fire2/fire3; port 1 or 2.")
async def c64_joystick(inputs: list[str], port: int = 2,
                       transition: Literal["tap", "press", "release"] = "tap") -> dict[str, Any]:
    return await _call("POST", "/api/device/joystick", json={"inputs": inputs, "port": port, "transition": transition})


@mcp.tool(description="Release every injected key and joystick input (emergency stop).")
async def c64_release_all() -> dict[str, Any]:
    return await _call("POST", "/api/device/release-all")


@mcp.tool(description="Open the Ultimate menu (verified by reading the menu screen when supported).")
async def c64_open_menu() -> dict[str, Any]:
    return await _call("POST", "/api/device/menu", json={"action": "open"})


@mcp.tool(description="Read the Ultimate menu as text with the selected row; null when the menu is closed.")
async def c64_read_menu() -> dict[str, Any]:
    data = await _call("GET", "/api/device/menu")
    screen = data.get("screen")
    if not screen:
        return {"open": False}
    return {"open": True, "text": screen["text"], "selectedRow": screen["selectedRow"],
            "selectedText": screen["selectedText"]}


@mcp.tool(description="Navigate the open Ultimate menu one step: up, down, left, right, return, back, exit, "
                      "home, page_up, page_down. Each step is verified; nothing is auto-repeated.")
async def c64_menu_navigate(action: str) -> dict[str, Any]:
    return await _call("POST", "/api/device/menu", json={"action": action})


@mcp.tool(description="Play a SID tune from the library by title.")
async def c64_play_sid(title: str) -> dict[str, Any]:
    return await _call("POST", "/api/intent", json={"intent": {"intent": "PLAY_SID", "game": title}})


@mcp.tool(description="Run a natural-language command (same parser as the console command bar). "
                      "Power off always requires human confirmation in the UI.")
async def c64_command(text: str) -> dict[str, Any]:
    return await _call("POST", "/api/command", json={"text": text[:300], "confirm": False})


@mcp.tool(description="See the C64 screen right now. Returns a PNG image of the current picture "
                      "(pixel-perfect, 768×544). Nothing is saved.")
async def c64_screenshot() -> Image:
    async with httpx.AsyncClient(base_url=API_BASE, timeout=30, headers=HEADERS) as client:
        r = await client.get("/api/screen.png")
    if r.status_code >= 400:
        raise RuntimeError(f"{r.status_code}: {r.text[:200]}")
    return Image(data=r.content, format="png")


@mcp.tool(description="Save a screenshot to the console's Gallery (optionally named). Returns its name/URL.")
async def c64_save_screenshot(title: str | None = None) -> dict[str, Any]:
    return await _call("POST", "/api/screenshots", json={"title": title} if title else {})


@mcp.tool(description="What is playing now: current game, disk number/count, and the last launch progress.")
async def c64_now_playing() -> dict[str, Any]:
    data = await _call("GET", "/api/current-session")
    s, job = data.get("session") or {}, data.get("job") or {}
    return {"title": s.get("title"), "format": s.get("format"), "disk": s.get("currentDisk"),
            "diskCount": s.get("diskCount"), "launch": {k: job.get(k) for k in ("title", "status", "error", "method")}
            if job else None}


@mcp.tool(description="List the local library: 'favorites', 'recent' (recently played) or 'all'.")
async def c64_list_library(which: Literal["all", "favorites", "recent"] = "all", limit: int = 30) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"limit": max(1, min(limit, 100))}
    if which != "all":
        params[which] = True
    data = await _call("GET", "/api/library", params=params)
    return [{k: g.get(k) for k in ("id", "title", "publisher", "year", "format", "numDisks", "favorite", "playCount")}
            for g in data["items"]]


@mcp.tool(description="Search the Assembly64 online catalog (CSDB, Gamebase64, OneLoad64, HVSC…). "
                      "kind: games, music, demos, tools or all. Use c64_play_catalog to start a result.")
async def c64_search_catalog(query: str, kind: Literal["games", "music", "demos", "tools", "all"] = "games",
                             ) -> list[dict[str, Any]]:
    data = await _call("GET", "/api/catalog/search", params={"q": query[:80], "kind": kind})
    return [{k: r.get(k) for k in ("id", "category", "name", "group", "year", "source")} for r in data[:25]]


@mcp.tool(description="Download (if needed) and start an entry from c64_search_catalog, by its id and category.")
async def c64_play_catalog(id: str, category: int, name: str | None = None) -> dict[str, Any]:
    return await _call("POST", "/api/catalog/play", json={"id": id, "category": category, "name": name})


@mcp.tool(description="Insert the previous disk of the current multi-disk game.")
async def c64_previous_disk() -> dict[str, Any]:
    return await _call("POST", "/api/session/previous-disk")


@mcp.tool(description="Start or stop recording the C64 (video + sound) to an MP4 in the Gallery.")
async def c64_record(action: Literal["start", "stop"]) -> dict[str, Any]:
    if action == "start":
        return await _call("POST", "/api/live/record/start")
    return await _call("POST", "/api/live/stop")


def set_api_base(url: str) -> None:
    """Used when the MCP endpoint is served by the console itself (tools call its local API)."""
    global API_BASE
    API_BASE = url.rstrip("/")


def main() -> None:
    parser = argparse.ArgumentParser(description="C64 Ultimate MCP server")
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    parser.add_argument("--port", type=int, default=8065)
    parser.add_argument("--host", default="127.0.0.1", help="bind address for --http (0.0.0.0 in Docker)")
    args = parser.parse_args()
    if args.http:
        mcp.run("streamable-http", host=args.host, port=args.port)
    else:
        mcp.run("stdio")


if __name__ == "__main__":
    main()
