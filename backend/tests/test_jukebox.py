import json
import struct

import pytest
from test_ask import FakeModel

from app.services.assembly64 import DownloadedFile
from app.services.jukebox import composer_matches, group_token, parse_psid, pretty_composer

CATALOG = {"ASSEMBLY64_URL": "https://example.test", "ASSEMBLY64_CLIENT_ID": "Spiffy"}


def make_sid(title="Commando", author="Rob Hubbard", released="1985 Elite", songs=3, start=1, magic=b"PSID"):
    def field(s):
        return s.encode("latin-1")[:32].ljust(32, b"\0")
    head = magic + struct.pack(">HHHHHHHI", 2, 0x7C, 0, 0x1000, 0x1003, songs, start, 0)
    head += field(title) + field(author) + field(released)
    head = head.ljust(0x7C, b"\0")
    return head + b"\x00\x10" + b"\x60" * 64


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class FakeA64:
    """Stands in for Assembly64Client: no network."""

    def __init__(self):
        self.calls = []
        self.downloads = 0
        self.names = {
            "Commando": [{"id": "100", "category": 18, "name": "Commando", "group": "Hubbard_Rob", "source": "HVSC music",
                          "rating": 5},
                         {"id": "101", "category": 4, "name": "Commando Remix", "group": "Booze Design", "source": "CSDB music"},
                         {"id": "102", "category": 16, "name": "Commando", "group": "Elite", "source": "Gamebase64"}],
            "Wizball": [{"id": "200", "category": 18, "name": "Wizball", "group": "Galway_Martin", "source": "HVSC music"}],
        }
        self.groups = {"Hubbard": [{"id": "100", "category": 18, "name": "Commando", "group": "Hubbard_Rob", "rating": 5},
                                   {"id": "103", "category": 18, "name": "Monty_on_the_Run", "group": "Hubbard_Rob", "rating": 9},
                                   {"id": "104", "category": 19, "name": "Delta", "group": "Hubbard_Rob", "rating": 7}]}

    async def search(self, name, kind=None, repo=None, ftype=None, group=None, offset=0, count=40):
        self.calls.append(("search", name, group))
        return [dict(r) for r in self.names.get(name, [])]

    async def _get(self, path, params=None, timeout=None):
        self.calls.append(("aql", params["query"]))
        q = params["query"]
        hits = [r for g, rows in self.groups.items() if g.lower() in q.lower() for r in rows]
        return FakeResponse(hits)

    async def entries(self, entry_id, category):
        self.calls.append(("entries", entry_id, category))
        return [{"id": 0, "path": "readme.txt", "size": 10}, {"id": 1, "path": "Commando.sid", "size": 1805}]

    async def download(self, entry_id, category, content_id, fallback_name="download.bin"):
        self.downloads += 1
        return DownloadedFile(filename="Commando.sid", data=make_sid(), checksum_ok=True)


def _fake_catalog(c, monkeypatch):
    fake = FakeA64()
    monkeypatch.setattr(c.app.state.container.catalog, "client", lambda: fake)
    return fake


def _spy_upload(c, monkeypatch):
    client = c.app.state.container.device.client
    uploads = []
    real = client.sid_play_upload

    async def spy(data, songnr=None, filename="tune.sid"):
        uploads.append((len(data), songnr, filename))
        await real(data, songnr, filename)
    monkeypatch.setattr(client, "sid_play_upload", spy)
    return uploads


# ------------------------------------------------------------------ helpers
def test_parse_psid():
    info = parse_psid(make_sid(songs=5, start=2))
    assert info["format"] == "PSID" and info["version"] == 2 and info["dataOffset"] == 0x7C
    assert (info["title"], info["author"], info["released"]) == ("Commando", "Rob Hubbard", "1985 Elite")
    assert (info["songs"], info["startSong"], info["initAddress"], info["playAddress"]) == (5, 2, 0x1000, 0x1003)
    assert parse_psid(make_sid(magic=b"RSID", start=9, songs=2))["startSong"] == 1     # out of range → 1
    assert parse_psid(make_sid(title="H\xe4nsel"))["title"] == "H\xe4nsel"               # latin-1
    for bad in (b"", b"PSID" + b"\0" * 10, b"MZ" + b"\0" * 200):
        with pytest.raises(ValueError):
            parse_psid(bad)


def test_names():
    assert pretty_composer("Hubbard_Rob") == "Rob Hubbard"
    assert pretty_composer("van_Rijn_Ramiro") == "Ramiro van Rijn"
    assert pretty_composer("Maniacs_of_Noise") == "Maniacs of Noise"
    assert pretty_composer("Booze Design") == "Booze Design" and pretty_composer(None) is None
    assert group_token("Rob Hubbard") == "Hubbard" and group_token("Galway_Martin") == "Galway_Martin"
    assert group_token('x") | (y') == "y" and group_token('"()') is None                 # no AQL injection
    assert composer_matches("Hubbard_Rob", "Rob Hubbard") and not composer_matches("Galway_Martin", "Rob Hubbard")


# ------------------------------------------------------------------ search
def test_search_maps_hvsc_tunes(app_client, monkeypatch):
    with app_client() as c:
        assert c.get("/api/jukebox/search", params={"q": "Commando"}).status_code == 409   # catalog not set up
    with app_client(**CATALOG) as c:
        fake = _fake_catalog(c, monkeypatch)
        tunes = c.get("/api/jukebox/search", params={"q": "Commando"}).json()["tunes"]
        assert [(t["id"], t["category"]) for t in tunes] == [("100", 18), ("101", 4)]       # no Gamebase entries
        assert tunes[0]["title"] == "Commando" and tunes[0]["composer"] == "Rob Hubbard"
        assert tunes[0]["source"] == "HVSC music"
        by = c.get("/api/jukebox/search", params={"composer": "Rob Hubbard"}).json()["tunes"]
        assert {t["title"] for t in by} == {"Commando", "Monty on the Run", "Delta"}
        assert ("aql", '(group:"Hubbard")') in fake.calls                                    # group-only query


# ------------------------------------------------------------------ play / stop
def test_play_downloads_once_then_uses_the_cache(app_client, monkeypatch):
    with app_client(**CATALOG) as c:
        cont = c.app.state.container
        assert cont.device.connected and cont.device.caps.usable("sidPlayback")             # the simulator plays SIDs
        fake = _fake_catalog(c, monkeypatch)
        uploads = _spy_upload(c, monkeypatch)
        r = c.post("/api/jukebox/play", json={"id": "100", "category": 18})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["track"]["title"] == "Commando" and body["track"]["composer"] == "Rob Hubbard"
        assert body["sid"] == {"title": "Commando", "author": "Rob Hubbard", "released": "1985 Elite", "songs": 3,
                               "startSong": 1, "format": "PSID"}
        assert fake.downloads == 1 and len(uploads) == 1 and uploads[0][2] == "Commando.sid"
        assert (cont.settings.data_path / "sidcache" / "18-100" / "Commando.sid").is_file()
        r = c.post("/api/jukebox/play", json={"id": "100", "category": 18, "song": 2, "title": "Commando (Hi)"})
        assert r.status_code == 200 and r.json()["track"]["song"] == 2
        assert fake.downloads == 1 and len(uploads) == 2 and uploads[1][1] == 2               # cached
        assert len([x for x in fake.calls if x[0] == "entries"]) == 1
        assert c.post("/api/jukebox/play", json={"id": "100", "category": 18, "song": 4}).status_code == 400
        assert c.post("/api/jukebox/play", json={"id": "100", "category": 16}).status_code == 400   # not music
        assert c.post("/api/jukebox/play", json={"id": "../x", "category": 18}).status_code == 400
        plays = c.get("/api/jukebox/history").json()["plays"]
        assert [p["title"] for p in plays] == ["Commando (Hi)", "Commando"]
        assert c.get("/api/jukebox/status").json()["nowPlaying"]["song"] == 2
        assert c.post("/api/jukebox/stop").json() == {"ok": True, "reset": True}
        assert c.get("/api/jukebox/status").json()["nowPlaying"] is None


def test_play_needs_a_connected_c64_with_sidplay(app_client, monkeypatch):
    with app_client(**CATALOG) as c:
        cont = c.app.state.container
        _fake_catalog(c, monkeypatch)
        uploads = _spy_upload(c, monkeypatch)
        cont.device.caps.set("sidPlayback", "unsupported", "test")
        r = c.post("/api/jukebox/play", json={"id": "100", "category": 18})
        assert r.status_code == 409
        cont.device.caps.set("machineReset", "unsupported", "test")
        r = c.post("/api/jukebox/stop")
        assert r.status_code == 409 and "reset button" in r.json()["detail"]
        monkeypatch.setattr(cont.device, "connected", False)
        assert c.post("/api/jukebox/play", json={"id": "100", "category": 18}).status_code == 503
        assert c.post("/api/jukebox/stop").status_code == 503
        assert uploads == []


# ------------------------------------------------------------------ stations
def test_station_keeps_only_picks_found_in_the_catalog(app_client, monkeypatch):
    reply = {"name": "Hubbard & friends", "description": "Classic SID.",
             "picks": [{"title": "Commando", "composer": "Rob Hubbard"},
                       {"title": "Wizball", "composer": "Martin Galway", "id": "999", "url": "https://evil.test/x.sid"},
                       {"title": "Not A Real Tune", "composer": "Nobody"},
                       {"title": "", "composer": "Rob Hubbard"},
                       "junk"],
             "composers": ["Hubbard_Rob"]}
    with app_client(**CATALOG) as c:
        cont = c.app.state.container
        cont.provider = FakeModel(cont.settings, json.dumps(reply))
        _fake_catalog(c, monkeypatch)
        r = c.post("/api/jukebox/stations", json={"prompt": "Rob Hubbard classics"})
        assert r.status_code == 200, r.text
        st = r.json()
        ids = [(t["id"], t["category"]) for t in st["tracks"]]
        assert ids[:2] == [("100", 18), ("200", 18)]                                          # HVSC, composer match
        assert ("999", 18) not in ids and all(t["durationS"] == 180 for t in st["tracks"])
        assert set(ids) == {("100", 18), ("200", 18), ("103", 18), ("104", 19)}               # composer pick expanded
        assert st["dropped"] == 1 and st["name"] == "Hubbard & friends"
        listing = c.get("/api/jukebox/stations").json()
        assert [s["id"] for s in listing["stations"]] == [st["id"]] and len(listing["suggestions"]) == 6
        assert c.get(f"/api/jukebox/stations/{st['id']}").json()["tracks"] == st["tracks"]
        assert c.delete(f"/api/jukebox/stations/{st['id']}").json() == {"ok": True}
        assert c.get(f"/api/jukebox/stations/{st['id']}").status_code == 404


def test_station_errors_are_409(app_client, monkeypatch):
    with app_client(**CATALOG) as c:
        cont = c.app.state.container
        _fake_catalog(c, monkeypatch)
        cont.provider = FakeModel(cont.settings, json.dumps({"name": "x", "picks": [{"title": "Nope", "composer": "Nobody"}]}))
        assert c.post("/api/jukebox/stations", json={"prompt": "nothing"}).status_code == 409
        cont.provider = FakeModel(cont.settings, "not json at all")
        assert c.post("/api/jukebox/stations", json={"prompt": "nothing"}).status_code == 409
