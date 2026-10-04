"""⬆ Update channel: release comparison, no-source message, and Update now needs an admin."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx

from app.services.app_update import AppUpdate, _version_tuple


def _upd(repo, handler):
    u = AppUpdate(lambda: SimpleNamespace(UPDATE_REPO=repo), "0.5.0")
    u.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return u


def test_version_compare():
    assert _version_tuple("v0.6.0") > _version_tuple("0.5.0")
    assert _version_tuple("0.5.0") == _version_tuple("v0.5.0")
    assert _version_tuple("1.0") > _version_tuple("0.9.9")


def test_newer_release_found():
    u = _upd("owner/c64-console", lambda r: httpx.Response(200, json={
        "tag_name": "v0.6.0", "html_url": "https://github.com/owner/c64-console/releases/tag/v0.6.0", "body": "BBS!"}))
    st = asyncio.run(u.check())
    assert st["newer"] is True and st["latest"] == "v0.6.0" and st["source"] == "release" and st["error"] is None


def test_same_version_and_errors():
    assert asyncio.run(_upd("o/r", lambda r: httpx.Response(200, json={"tag_name": "v0.5.0"})).check())["newer"] is False
    st = asyncio.run(_upd("o/r", lambda r: httpx.Response(404)).check())
    assert "no published releases" in st["error"]
    bad = AppUpdate(lambda: SimpleNamespace(UPDATE_REPO="not a repo; rm -rf"), "0.5.0")
    assert bad.repo == ""                                   # only owner/name is ever used


def test_update_endpoints(app_client):
    with app_client() as client:
        st = client.get("/api/system/update").json()
        assert st["version"] == "0.5.0" and "command" in st
        assert client.post("/api/system/update/apply").status_code == 403    # not this computer, no password
