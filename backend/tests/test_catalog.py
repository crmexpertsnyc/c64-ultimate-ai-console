import hashlib
import json
import time

import httpx
import pytest
from helpers import make_d64, make_sid

from app.services import assembly64 as a64
from app.services.assembly64 import build_query, category_info, choose_files
from app.services.catalog import split_author

# A fake Assembly64 server with the same routes/headers as hackerswithstyle.se.
SEARCH = {
    "bruce lee": [
        {"category": 0, "group": "Nice Boys Software", "id": "260775", "name": "Bruce Lee", "year": 1990},
        {"category": 16, "id": "9805", "name": "Bruce Lee", "year": 0},
        {"category": 16, "id": "1135", "name": "Bruce Lee", "year": 0},
        {"category": 16, "id": "31205", "name": "Bruce Lee II", "year": 0},
    ],
    "summer games": [{"category": 16, "id": "777", "name": "Summer Games", "year": 1984, "group": "Epyx"}],
    "boulder dash": [{"category": 16, "id": "993", "name": "Boulder Dash +"},
                     {"category": 16, "id": "994", "name": "Boulder Dash"}],
    "commando": [
        {"category": 18, "id": "1", "name": "Commando", "group": "Cadaver"},
        {"category": 18, "id": "2", "name": "Commando", "group": "Hubbard_Rob"},
    ],
    "tapeonly": [{"category": 35, "id": "1219", "name": "Tapeonly"}],
    "last ninja": [{"category": 16, "id": "9747", "name": "Last Ninja_ The"},
                   {"category": 33, "id": "ef01", "name": "last ninja_ the (by $olo1870) [easyflash]"}],
}
ENTRIES = {
    "1135/16": [{"id": 0, "path": "BRUCELEE.D64", "size": 174848}],
    "9805/16": [{"id": 0, "path": "BRUCELEE.D64", "size": 174848}, {"id": 1, "path": "readme.txt", "size": 10}],
    "260775/0": [{"id": 0, "path": "brucelee-nbs.d64", "size": 174848}],
    "777/16": [{"id": 0, "path": "SUMMER1A.G64", "size": 333744},
               {"id": 1, "path": "SUMMER1B.D64", "size": 174848}],
    "994/16": [{"id": 0, "path": "BDASH.D64", "size": 174848}],
    "2/18": [{"id": 0, "path": "Commando.sid", "size": 130}],
    "1219/35": [{"id": 0, "path": "Tapeonly.tap", "size": 1000}],
    "9747/16": [{"id": 0, "path": "NINJA_A.D64", "size": 174848}, {"id": 1, "path": "NINJA_B.D64", "size": 174848}],
    "ef01/33": [{"id": 0, "path": "Last Ninja_ The [EasyFlash].crt", "size": 80}],
}
FILES = {"BRUCELEE.D64": make_d64("BRUCE LEE"), "SUMMER1A.G64": b"GCR-1541" + bytes(64),
         "SUMMER1B.D64": make_d64("SG2"), "Commando.sid": make_sid(),
         "NINJA_A.D64": make_d64("NINJA A"), "NINJA_B.D64": make_d64("NINJA B"),
         "Last Ninja_ The [EasyFlash].crt": b"C64 CARTRIDGE   " + bytes(64)}


def fake_server(request: httpx.Request) -> httpx.Response:
    if request.headers.get("Client-Id") != "Spiffy":
        return httpx.Response(464)
    path = request.url.path
    if path.startswith("/leet/search/aql/"):
        q = request.url.params["query"].lower()
        name = q.split('name:"')[1].split('"')[0]
        results = SEARCH.get(name, [])
        if 'group:"' in q:
            grp = q.split('group:"')[1].split('"')[0]
            results = [r for r in results if grp in (r.get("group") or "").lower()]
        return httpx.Response(200, json=results)
    if path.startswith("/leet/search/entries/"):
        key = path.removeprefix("/leet/search/entries/")
        return httpx.Response(200, json={"contentEntry": ENTRIES.get(key, [])})
    if path.startswith("/leet/search/bin/"):
        eid, cat, cid = path.removeprefix("/leet/search/bin/").split("/")
        entry = next(e for e in ENTRIES[f"{eid}/{cat}"] if str(e["id"]) == cid)
        data = FILES[entry["path"]]
        return httpx.Response(200, content=data, headers={
            "content-type": "application/octet-stream", "filename": entry["path"],
            "checksum": hashlib.md5(data).hexdigest()})
    return httpx.Response(404)


@pytest.fixture
def fake_catalog(monkeypatch):
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(fake_server)
        return real(*args, **kwargs)

    monkeypatch.setattr(a64.httpx, "AsyncClient", factory)


def test_helpers():
    assert build_query('bruce "lee"', "games", group="Hubbard") == '(name:"bruce lee") & (category:games) & (group:"Hubbard")'
    assert category_info(16)["source"] == "Gamebase64" and category_info(18)["kind"] == "music"
    assert split_author("Commando by Rob Hubbard") == ("Commando", "Hubbard")
    assert split_author("Bruce Lee") == ("Bruce Lee", None)
    files = choose_files(ENTRIES["777/16"])
    assert [f["path"] for f in files] == ["SUMMER1A.G64", "SUMMER1B.D64"]
    same_disk = [{"id": 0, "path": "x side1.g64"}, {"id": 1, "path": "x side1.d64"}, {"id": 2, "path": "x side2.d64"}]
    assert [f["path"] for f in choose_files(same_disk)] == ["x side1.d64", "x side2.d64"]
    assert choose_files(ENTRIES["9805/16"])[0]["path"] == "BRUCELEE.D64"
    assert choose_files(ENTRIES["1219/35"]) == []


async def test_client_rejects_unknown_client_id(fake_catalog):
    with pytest.raises(a64.Assembly64AuthError):
        await a64.Assembly64Client("https://example.test", "nope").search("bruce lee")


async def test_checksum_mismatch_detected(monkeypatch):
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(
            lambda r: httpx.Response(200, content=b"abc", headers={"checksum": "0" * 32, "filename": "x.prg"}))
        return real(*args, **kwargs)

    monkeypatch.setattr(a64.httpx, "AsyncClient", factory)
    with pytest.raises(a64.Assembly64Error, match="checksum"):
        await a64.Assembly64Client("https://example.test", "Spiffy").download("1", 0, 0)


def _wait_job(c, job_id):
    for _ in range(200):
        job = c.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_play_command_falls_back_to_catalog(app_client, fake_catalog):
    with app_client(ASSEMBLY64_URL="https://example.test", ASSEMBLY64_CLIENT_ID="Spiffy") as c:
        sim = c.app.state.container.device.simulator
        r = c.post("/api/command", json={"text": "Play Bruce Lee"}).json()
        assert r["ok"], r
        assert "Gamebase64" in r["message"]  # curated source preferred over the CSDB crack
        # Of two Gamebase "Bruce Lee" entries, the older id (original release) wins.
        assert "16-1135" in c.get(f"/api/games/{r['data']['gameId']}").json()["media"][0]["path"]
        job = _wait_job(c, r["data"]["job"]["id"])
        assert job["status"] == "done", job
        assert sim.drives["a"]["image_file"] == "uploaded.d64"

        game = c.get(f"/api/games/{r['data']['gameId']}").json()
        assert game["title"] == "Bruce Lee" and "assembly64" in game["tags"]
        assert len(game["media"]) == 1  # readme.txt skipped

        # Second request plays the cached local copy — no new download.
        r2 = c.post("/api/command", json={"text": "Play Bruce Lee"}).json()
        assert r2["ok"] and "Assembly64" not in r2["message"]


def test_multidisk_catalog_entry_supports_disk_swap(app_client, fake_catalog):
    with app_client(ASSEMBLY64_URL="https://example.test", ASSEMBLY64_CLIENT_ID="Spiffy") as c:
        r = c.post("/api/command", json={"text": "Load Summer Games"}).json()
        assert r["ok"], r
        _wait_job(c, r["data"]["job"]["id"])
        session = c.get("/api/current-session").json()["session"]
        assert session["diskCount"] == 2 and session["title"] == "Summer Games"
        assert sorted(d["diskNumber"] for d in session["disks"]) == [1, 2]
        r = c.post("/api/command", json={"text": "Put disk 2 in"}).json()
        assert r["ok"], r


def test_ambiguous_sid_asks_and_by_composer_resolves(app_client, fake_catalog):
    with app_client(ASSEMBLY64_URL="https://example.test", ASSEMBLY64_CLIENT_ID="Spiffy") as c:
        r = c.post("/api/command", json={"text": "Play the SID file Commando"}).json()
        assert not r["ok"] and len(r["onlineCandidates"]) == 2
        r = c.post("/api/command", json={"text": "play the sid commando by rob hubbard"}).json()
        assert r["ok"], r
        assert "Hubbard" in r["message"]


def test_catalog_endpoints(app_client, fake_catalog):
    with app_client(ASSEMBLY64_URL="https://example.test", ASSEMBLY64_CLIENT_ID="Spiffy") as c:
        assert c.get("/api/catalog").json()["configured"] is True
        results = c.get("/api/catalog/search", params={"q": "bruce lee"}).json()
        assert results[0]["source"] == "CSDB games" and results[1]["source"] == "Gamebase64"
        files = c.get("/api/catalog/entries/16/9805").json()
        assert [f["selected"] for f in files] == [True, False]
        r = c.post("/api/catalog/fetch", json={"id": "9805", "category": 16, "name": "Bruce Lee"}).json()
        again = c.get("/api/catalog/search", params={"q": "bruce lee"}).json()
        assert next(x for x in again if x["id"] == "9805")["gameId"] == r["gameId"]
        bad = c.post("/api/catalog/fetch", json={"id": "1219", "category": 35, "name": "Tapeonly"})
        assert bad.status_code == 502 and "TAP" in bad.json()["detail"]
        meta = json.loads((c.app.state.container.catalog.cache_root / "16-9805" / "assembly64.json").read_text())
        assert meta["id"] == "9805"


def test_literal_title_beats_normalised_twin(app_client, fake_catalog):
    with app_client(ASSEMBLY64_URL="https://example.test", ASSEMBLY64_CLIENT_ID="Spiffy") as c:
        found = __import__("asyncio").run(c.app.state.container.catalog.find_for_play("Boulder Dash", "games"))
        assert found[0]["name"] == "Boulder Dash"


def test_catalog_not_configured(app_client):
    with app_client() as c:
        assert c.get("/api/catalog").json()["configured"] is False
        r = c.post("/api/command", json={"text": "Play Bruce Lee"}).json()
        assert not r["ok"] and "Assembly64" in r["message"]


class _TitleModel:
    """Fake LLM that identifies titles from descriptions."""
    name, model, base_url, configured = "fake", "fake", "http://localhost", True

    async def complete(self, system, user):
        if "karate" in user:
            return '{"title": "Bruce Lee", "author": "", "confidence": 0.9}'
        return '{"intent": "UNKNOWN"}'

    def describe(self):
        return {"provider": "fake", "model": "fake", "configured": True, "local": True}


def test_description_is_identified_by_llm_then_played(app_client, fake_catalog):
    with app_client(ASSEMBLY64_URL="https://example.test", ASSEMBLY64_CLIENT_ID="Spiffy") as c:
        c.app.state.container.engine.provider = _TitleModel()
        r = c.post("/api/command", json={"text": "play that karate game with the yellow jumpsuit guy"}).json()
        assert r["ok"], r
        assert "Bruce Lee" in r["message"] and "I think you mean" in r["message"]


def test_play_in_browser_prefers_cartridge_and_leaves_c64_alone(app_client, fake_catalog):
    with app_client(ASSEMBLY64_URL="https://example.test", ASSEMBLY64_CLIENT_ID="Spiffy") as c:
        sim = c.app.state.container.device.simulator
        before = dict(sim.drives["a"])
        r = c.post("/api/command", json={"text": "play last ninja in the browser"}).json()
        assert r["ok"], r
        assert r["data"]["target"] == "browser" and r["data"]["navigate"] == f"/emulate/{r['data']['gameId']}"
        game = c.get(f"/api/games/{r['data']['gameId']}").json()
        assert game["title"] == "The Last Ninja (EasyFlash)" and game["format"] == "crt"
        assert sim.drives["a"] == before  # nothing was loaded on the C64
        info = c.get(f"/api/games/{game['id']}/emulator").json()
        assert info["bundleUrl"] is None and info["files"][0]["format"] == "crt"


def test_play_on_c64_keeps_curated_disk_version_and_bundles_disks(app_client, fake_catalog):
    import io
    import zipfile
    with app_client(ASSEMBLY64_URL="https://example.test", ASSEMBLY64_CLIENT_ID="Spiffy") as c:
        r = c.post("/api/command", json={"text": "play last ninja"}).json()
        assert r["ok"], r
        game = c.get(f"/api/games/{r['data']['gameId']}").json()
        assert game["title"] == "The Last Ninja" and game["format"] == "d64"  # tidied Gamebase name
        info = c.get(f"/api/games/{game['id']}/emulator").json()
        assert info["bundleUrl"].startswith(f"/api/games/{game['id']}/emulator.zip")
        z = zipfile.ZipFile(io.BytesIO(c.get(info["bundleUrl"]).content))
        playlist = z.read("disks.m3u").decode().split()
        assert len(playlist) == 2 and all(n in z.namelist() for n in playlist)
