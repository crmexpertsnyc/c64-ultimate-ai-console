import pytest

pytest.importorskip("mcp")

from app.mcp import server as mcp_server  # noqa: E402

EXPECTED = {"c64_device_info", "c64_search_games", "c64_play_game", "c64_reset", "c64_mount_disk", "c64_next_disk",
            "c64_press_key", "c64_type_text", "c64_joystick", "c64_open_menu", "c64_read_menu", "c64_play_sid"}


async def test_tools_registered_and_no_low_level_access():
    names = {t.name for t in await mcp_server.mcp.list_tools()}
    assert names >= EXPECTED
    forbidden = ("mem", "poke", "peek", "write", "config", "rom", "power", "vision", "raw", "http")
    assert not [n for n in names if any(f in n for f in forbidden)]


async def test_tools_proxy_to_api_with_mcp_source(app_client, monkeypatch):
    import httpx

    with app_client() as c:
        transport = httpx.MockTransport(lambda req: _forward(c, req))
        real_client = httpx.AsyncClient

        def fake_client(*args, **kwargs):
            kwargs["transport"] = transport
            return real_client(*args, **kwargs)

        monkeypatch.setattr(mcp_server.httpx, "AsyncClient", fake_client)
        info = await mcp_server.c64_device_info()
        assert info["connected"] is True
        menu = await mcp_server.c64_open_menu()
        assert menu["verified"]
        read = await mcp_server.c64_read_menu()
        assert read["open"] and read["selectedText"]
        result = await mcp_server.c64_command("power off")
        assert result["ok"] is False and "MCP" in result["message"]
        audit = c.get("/api/audit", params={"source": "mcp"}).json()
        assert audit and all(e["source"] == "mcp" for e in audit)


def _forward(test_client, request):
    import httpx
    r = test_client.request(request.method, request.url.path, params=dict(request.url.params),
                            content=request.content, headers={"X-C64-Source": request.headers.get("X-C64-Source", ""),
                                                              "Content-Type": "application/json"})
    return httpx.Response(r.status_code, content=r.content, headers={"content-type": r.headers.get("content-type", "")})


async def test_new_tools_registered():
    names = {t.name for t in await mcp_server.mcp.list_tools()}
    assert {"c64_screenshot", "c64_now_playing", "c64_search_catalog", "c64_play_catalog", "c64_previous_disk",
            "c64_record", "c64_list_library", "c64_save_screenshot"} <= names


def test_mcp_endpoint_mounted_and_guarded(app_client):
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
    hdrs = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", "Host": "localhost:8064"}
    with app_client() as c:
        ok = c.post("/mcp", json=body, headers=hdrs)
        assert ok.status_code == 200, ok.text
        assert c.post("/mcp", json=body, headers={**hdrs, "Host": "10.0.0.5:8064"}).status_code == 403
        assert c.post("/mcp", json=body, headers={**hdrs, "Origin": "http://evil.example"}).status_code == 403
        c.put("/api/settings", json={"MCP_TOKEN": "tok-123"})
        assert c.post("/mcp", json=body, headers=hdrs).status_code == 401
        assert c.post("/mcp", json=body, headers={**hdrs, "Authorization": "Bearer wrong"}).status_code == 401
        good = c.post("/mcp", json=body, headers={**hdrs, "Authorization": "Bearer tok-123", "Host": "10.0.0.5:8064"})
        assert good.status_code == 200, good.text
        info = c.get("/api/mcp/info").json()
        assert info["enabled"] and info["tokenSet"] and "c64_screenshot" in info["tools"]
        assert info["stdio"]["script"].endswith("c64_mcp.py")


def test_mcp_can_be_disabled(app_client):
    with app_client(MCP_ENABLED=False) as c:
        assert c.post("/mcp", json={}).status_code in (404, 405)  # not mounted at all
