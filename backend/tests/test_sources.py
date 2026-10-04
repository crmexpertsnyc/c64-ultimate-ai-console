import io
import zipfile

import httpx
import pytest

from app.services import sources

REAL_ARCHIVE_MATCHES = sources.archive_matches  # (conftest stubs it for other tests)

ARCHIVE_SEARCH = {"response": {"docs": [
    {"identifier": "c64_Bubble_Bobble", "title": "Bubble Bobble", "emulator_ext": "d64", "collection": ["softwarelibrary_c64"]},
    {"identifier": "uta_Bubble_Bobble_1987_Firebird_2230", "title": "Bubble Bobble (1987 Firebird) [2230]", "year": "1987",
     "emulator_ext": "tap", "collection": ["ultimatetapearchive"]},
    {"identifier": "bad id!", "title": "Bubble Bobble"},
    {"identifier": "c64_Other", "title": "Some Other Game"}]}}
C64COM = ('<a onFocus="this.blur()" href="no-frame.php?showid=91&searchfor=bubble&from=0&range=10"><b>Bubble Bobble</b></a>'
          '<a href="no-frame.php?showid=1456&searchfor=bubble&from=0"><b>Bubble Dizzy</b></a>')
GTW = [{"id": 10902, "title": {"rendered": "Bubbles"}, "link": "https://www.gamesthatwerent.com/gtw64/bubbles-2/"},
       {"id": 5, "title": {"rendered": "Bubble Evil"}, "link": "https://evil.example/x"}]


def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, d in files.items():
            z.writestr(n, d)
    return buf.getvalue()


def handler(request: httpx.Request) -> httpx.Response:
    u = str(request.url)
    if "advancedsearch.php" in u:
        return httpx.Response(200, json=ARCHIVE_SEARCH)
    if "c64.com/games/no-frame.php" in u:
        return httpx.Response(200, text=C64COM)
    if "wp-json/wp/v2/gtw64?" in u:
        return httpx.Response(200, json=GTW)
    if u.startswith("https://archive.org/metadata/Multi_Disk"):
        return httpx.Response(200, json={"metadata": {"emulator_ext": "d64"}, "files": [
            {"name": "Game Disk 1.d64", "source": "original"}, {"name": "Game Disk 2.d64", "source": "original"},
            {"name": "Extras/manual.pdf", "source": "original"}]})
    if u.startswith("https://archive.org/metadata/c64_Bubble_Bobble"):
        return httpx.Response(200, json={"metadata": {"emulator_ext": "d64"},
                                         "files": [{"name": "Bubble_Bobble.d64", "source": "original"}, {"name": "x.png"}]})
    if u.startswith("https://archive.org/metadata/Evil"):
        return httpx.Response(200, json={"files": [{"name": "Evil.d64", "source": "original"}]})
    if u.startswith("https://archive.org/download/Evil/"):
        return httpx.Response(302, headers={"location": "https://evil.example/Evil.d64"})
    if u.startswith("https://archive.org/download/"):
        item = u.rsplit("/", 2)[-2]
        return httpx.Response(302, headers={"location": f"https://dn123.ca.archive.org/0/items/{item}/f.d64"})
    if "ca.archive.org" in u:
        return httpx.Response(200, content=b"D64" * 100)
    if "c64.com/games/download.php" in u:
        return httpx.Response(200, content=_zip({"Bubble Bobble.d64": b"x" * 10}),
                              headers={"content-disposition": 'attachment; Filename="bubble_bobble.zip"'})
    if "wp-json/wp/v2/gtw64/10902" in u:
        return httpx.Response(200, json={"link": "https://www.gamesthatwerent.com/gtw64/fire-breath/"})
    if u == "https://www.gamesthatwerent.com/gtw64/fire-breath/":
        return httpx.Response(200, text='<div id="tabs-downloads"><a href=https://www.gamesthatwerent.com/wp-content/'
                                        'uploads/gtw64/f/fire-breath/Game_Firebreath.zip>Game</a></div>')
    if "wp-content/uploads" in u:
        return httpx.Response(200, content=_zip({"firebreath.prg": b"\x01\x08"}))
    return httpx.Response(404)


@pytest.fixture
def fake_net(monkeypatch):
    real = httpx.AsyncClient

    def client(timeout=12):  # noqa: ANN001
        return real(transport=httpx.MockTransport(handler), follow_redirects=True, timeout=timeout)

    monkeypatch.setattr(sources, "_client", client)


async def test_search_all_merges_sources_and_filters(fake_net):
    r = await sources.search_all("Bubble Bobble")
    got = [(x["source"], x["id"]) for x in r["results"]]
    assert ("archive", "c64_Bubble_Bobble") in got and ("c64com", "91") in got
    assert ("archive", "bad id!") not in got and ("archive", "c64_Other") not in got      # invalid id / irrelevant
    assert all(x["source"] != "gtw" or x["url"].startswith("https://www.gamesthatwerent.com/") for x in r["results"])
    assert got[0][1] in ("c64_Bubble_Bobble", "91")                                        # exact titles first
    tape = next(x for x in r["results"] if x["id"].startswith("uta_"))
    assert tape["year"] == 1987 and "Tape" in tape["note"]
    assert {x["source"] for x in r["linkOuts"]} == {"gb64", "csdb", "lemon64"}
    with pytest.raises(sources.SourceError):
        await sources.search_all(" ")


async def test_fetch_downloads_only_from_the_archive(fake_net):
    name, data, page = await sources.fetch("archive", "c64_Bubble_Bobble")
    assert data.startswith(b"D64") and page == "https://archive.org/details/c64_Bubble_Bobble"
    name, data, _ = await sources.fetch("archive", "Multi_Disk")          # two disks → one zip
    assert name == "Multi_Disk.zip"
    assert sorted(zipfile.ZipFile(io.BytesIO(data)).namelist()) == ["Game Disk 1.d64", "Game Disk 2.d64"]
    name, data, _ = await sources.fetch("c64com", "91")
    assert name == "bubble_bobble.zip"
    name, data, _ = await sources.fetch("gtw", "10902")
    assert name == "Game_Firebreath.zip"
    with pytest.raises(sources.SourceError, match="left the archive"):
        await sources.fetch("archive", "Evil")                            # redirect off archive.org
    for bad in [("archive", "../etc"), ("c64com", "91; rm"), ("gtw", "x"), ("gb64", "1")]:
        with pytest.raises(sources.SourceError):
            await sources.fetch(*bad)


def test_import_source_endpoint(app_client, monkeypatch):
    async def fetch(source, ident):  # noqa: ANN001
        return "game.prg", b"\x01\x08\x00\x00", f"https://example.test/{ident}"

    monkeypatch.setattr(sources, "fetch", fetch)
    with app_client() as c:
        r = c.post("/api/library/import-source", json={"source": "archive", "id": "c64_Test", "title": "Test Game"})
        assert r.status_code == 200, r.text
        g = c.get(f"/api/games/{r.json()['gameId']}").json()
        assert g["title"] == "Test Game"
        assert c.post("/api/library/import-source", json={"source": "http", "id": "x"}).status_code == 422



async def test_archive_matches_skip_sequels(monkeypatch):
    async def fake_search(q):  # noqa: ANN001, ARG001
        return {"results": [
            {"source": "gtw", "id": "1", "url": "https://www.gamesthatwerent.com/gtw64/x/", "label": "Games That Weren't",
             "title": "Lotus Turbo Challenge 2", "note": None},
            {"source": "archive", "id": "a", "url": "https://archive.org/details/a", "label": "Internet Archive",
             "title": "Lotus Turbo Challenge (1990)(Gremlin)", "note": None},
            {"source": "c64com", "id": "7", "url": "https://www.c64.com/games/7", "label": "C64.com",
             "title": "Lotus Turbo Challenge Deluxe", "note": None}]}

    monkeypatch.setattr(sources, "search_all", fake_search)
    got = await REAL_ARCHIVE_MATCHES("Lotus Turbo Challenge")
    assert [(m["kind"], m["title"]) for m in got] == [("archive", "Lotus Turbo Challenge (1990)(Gremlin)"),
                                                    ("c64com", "Lotus Turbo Challenge Deluxe")]
