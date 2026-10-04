import asyncio

from helpers import make_d64

from app.services import imports as imp


def test_upload_adds_a_playable_game(app_client):
    data = make_d64("LETS INVADE", [("LI3", b"\x01\x08\xea")])
    with app_client() as c:
        r = c.post("/api/library/import", params={"filename": "lets_invade_3.d64", "title": "Let's Invade 3"}, content=data)
        assert r.status_code == 200, r.text
        gid = r.json()["gameId"]
        game = c.get(f"/api/games/{gid}").json()
        assert game["title"] == "Let's Invade 3" and "imported" in game["tags"] and game["format"] == "d64"
        assert c.get(f"/api/games/{gid}/emulator").json()["files"][0]["format"] == "d64"  # 💻 In browser works
        # The same file again: the same game, not a duplicate.
        again = c.post("/api/library/import", params={"filename": "copy.d64"}, content=data).json()
        assert again["gameId"] == gid


def test_upload_rejects_non_c64_files(app_client):
    with app_client() as c:
        r = c.post("/api/library/import", params={"filename": "setup.exe"}, content=b"MZ....")
        assert r.status_code == 400 and "not a C64 file" in r.json()["detail"]
        assert c.post("/api/library/import", params={"filename": "empty.prg"}, content=b"").status_code == 400


def test_url_import_only_from_csdb(app_client):
    with app_client() as c:
        for url in ["https://richard-tnd.itch.io/li3", "http://csdb.dk/x.d64", "https://evil.example/csdb.dk/a.d64"]:
            r = c.post("/api/library/import-url", json={"url": url})
            assert r.status_code == 400 and "CSDb" in r.json()["detail"], url


def test_find_elsewhere_classifies_sources():
    results = [
        {"title": "Let's Invade 3 by Richard of TND - itch.io", "url": "https://richard-tnd.itch.io/li3", "description": ""},
        {"title": "Richard of TND published Let's Invade 3", "url": "https://itch.io/e/32020449/x", "description": ""},
        {"title": "[CSDb] - Let's Invade 3 by TND (2025)", "url": "https://csdb.dk/release/?id=250001", "description": ""},
        {"title": "Let's Invade 3 - Commodore 64 Game", "url": "https://www.lemon64.com/game/lets-invade-3", "description": ""},
        {"title": "Some other game", "url": "https://foo.itch.io/other", "description": ""},
    ]

    async def search(q, key, count=8):  # noqa: ANN001
        return results

    class Client:
        async def entries(self, eid, cat):  # noqa: ANN001
            return [{"id": 0, "path": "li3.d64"}] if eid == "250001" and cat == 0 else []

    class Catalog:
        configured = True

        def client(self):
            return Client()

    async def not_playable(url):  # noqa: ANN001
        return url.endswith("/li3")

    imp._itch_playable, orig = not_playable, imp._itch_playable
    try:
        out = asyncio.run(imp.find_elsewhere("Let's Invade 3", "k", Catalog(), search=search))
    finally:
        imp._itch_playable = orig
    kinds = {o["kind"]: o for o in out}
    assert set(kinds) == {"itch", "csdb", "lemon64"}
    assert kinds["itch"]["url"] == "https://richard-tnd.itch.io/li3" and kinds["itch"]["playable"] is True
    assert kinds["csdb"]["catalog"]["id"] == "250001" and kinds["csdb"]["catalog"]["category"] == 0
    assert all("other" not in o["url"] for o in out)  # unrelated results dropped


def test_find_sources_needs_brave_key(app_client):
    with app_client() as c:
        assert c.get("/api/sources/find", params={"title": "x"}).status_code == 409
