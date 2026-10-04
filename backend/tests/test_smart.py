import json
from datetime import UTC, datetime, timedelta

from test_ask import FakeModel

from app.library.titles import title_key
from app.services import ai_json


def _game(c, title, **kw):
    from app.models.db import Game
    with c.app.state.container.sf() as s:
        g = Game(group_key=f"t:{title}", title=title, normalized_title=title_key(title), format=kw.pop("format", "d64"), **kw)
        s.add(g)
        s.commit()
        return g.id


def _model(c, reply):
    cont = c.app.state.container
    cont.provider = FakeModel(cont.settings, json.dumps(reply) if not isinstance(reply, str) else reply)
    return cont.provider


def test_fill_in_details_only_fills_blanks_and_tags_are_searchable(app_client, monkeypatch):
    async def search(q, key, count=5, timeout=10):  # noqa: ANN001
        return [{"title": "Bubble Bobble - GB64", "url": "https://example.test/bb", "description": "Firebird 1987"}]

    monkeypatch.setattr(ai_json, "brave_search", search)
    with app_client() as c:
        c.app.state.container.config.update({"BRAVE_API_KEY": "k"})
        m = _model(c, {"year": 1987, "publisher": "Firebird", "genre": "platform", "players": "1-2", "joystickPort": 2,
                       "description": "Two dragons blow bubbles to trap monsters.", "tags": ["co-op", "great music", "made-up"],
                       "sources": [1]})
        gid = _game(c, "Bubble Bobble", publisher="Taito (my edit)")
        r = c.post(f"/api/games/{gid}/details").json()
        assert r["genre"] == "Platform" and r["tags"] == ["co-op", "great music"]          # vocabulary enforced
        assert sorted(r["filled"]) == ["genre", "joystick_port", "players", "year"]         # publisher was set
        g = c.get(f"/api/games/{gid}").json()
        assert g["publisher"] == "Taito (my edit)" and g["year"] == 1987 and g["genre"] == "Platform"
        assert g["details"]["sources"][0]["url"] == "https://example.test/bb" and "co-op" in g["details"]["tags"]
        assert "Bubble Bobble" in [x["title"] for x in c.get("/api/library", params={"q": "great music"}).json()["items"]]
        assert "Bubble Bobble" in [x["title"] for x in c.get("/api/library", params={"q": "platform"}).json()["items"]]
        assert m.calls[0][1].startswith("Game: Bubble Bobble")
        # the whole library, in the background
        _game(c, "Summer Games")
        job = c.post("/api/library/details", json={"onlyMissing": True}).json()
        assert job["total"] == 1                                                            # BB already done


def test_fill_in_details_needs_ai(app_client):
    with app_client() as c:
        gid = _game(c, "Elite")
        assert c.post(f"/api/games/{gid}/details").status_code == 409
        assert c.post("/api/library/details").status_code == 409


def test_hint_levels_and_web_search(app_client, monkeypatch):
    queries = []

    async def search(q, key, count=5, timeout=10):  # noqa: ANN001
        queries.append(q)
        return [{"title": "Walkthrough", "url": "https://example.test/w", "description": "Go north first"}]

    monkeypatch.setattr(ai_json, "brave_search", search)
    with app_client() as c:
        c.app.state.container.config.update({"BRAVE_API_KEY": "k"})
        m = _model(c, {"hint": "Look closely at the lamp.", "confidence": "high", "sources": [1]})
        gid = _game(c, "Mission Impossible")
        r = c.post(f"/api/games/{gid}/hint", json={"screen": "YOU ARE IN A LOBBY. EXITS: NORTH", "level": 1}).json()
        assert r["hint"] == "Look closely at the lamp." and r["level"] == 1 and queries == []   # a nudge: no web search
        r = c.post(f"/api/games/{gid}/hint", json={"screen": "YOU ARE IN A LOBBY", "level": 2,
                                                   "previous": ["Look closely at the lamp."]}).json()
        assert queries and "Mission Impossible" in queries[0] and r["sources"][0]["url"] == "https://example.test/w"
        assert "go further than these" in m.calls[-1][1] and "DIRECTION" in m.calls[-1][0]
        assert c.post(f"/api/games/{gid}/hint", json={"level": 4}).status_code == 422
        assert c.post("/api/games/9999/hint", json={}).status_code == 404


def test_copilot_sanitises_commands_and_builds_the_map(app_client):
    with app_client() as c:
        _model(c, {"room": "Lobby", "exits": ["n", "E"], "inventory": ["badge"], "goal": "find the lift",
                   "suggestions": [{"command": "go north", "why": "an exit"}, {"command": "GET BADGE; RUN", "why": "x"},
                                   {"command": "LOOK", "why": "see more"}, {"command": "look", "why": "dup"}]})
        gid = _game(c, "Mission Impossible")
        r = c.post(f"/api/games/{gid}/copilot", json={"transcript": "I'M IN A LOBBY. OBVIOUS EXITS: NORTH, EAST",
                                                      "notes": {"map": {"Street": ["N"]}}}).json()
        assert [s["command"] for s in r["suggestions"]] == ["GO NORTH", "LOOK"]   # ';' rejected, duplicate dropped
        assert r["map"] == {"Street": ["N"], "Lobby": ["N", "E"]} and r["inventory"] == ["badge"]
        assert c.post(f"/api/games/{gid}/copilot", json={"transcript": "  "}).status_code == 400


def test_hang_rescue_offers_other_versions(app_client):
    with app_client() as c:
        stuck = _game(c, "The Last Ninja")
        cart = _game(c, "The Last Ninja (EasyFlash)", format="crt")
        _game(c, "Last Ninja, The", format="t64")
        _game(c, "Last Ninja 2")
        assert c.post(f"/api/games/{stuck}/hang").json()["hangs"] == 1
        r = c.get(f"/api/games/{stuck}/alternatives").json()
        alts = [(a["title"], a["kind"]) for a in r["alternatives"]]
        assert alts[0] == ("The Last Ninja (EasyFlash)", "library") and ("Last Ninja, The", "library") in alts
        assert all(a[0] != "Last Ninja 2" for a in alts) and r["hangs"] == 1
        assert r["alternatives"][0]["gameId"] == cart


def test_playlists(app_client):
    with app_client() as c:
        cont = c.app.state.container
        m = _model(c, {"name": "Friday Frenzy", "description": "Four players, lots of laughs.", "items": [
            {"title": "Summer Games", "players": "1-8", "minutes": 30, "note": "Take turns."},
            {"title": "Paperboy", "players": "1-2", "minutes": 10, "note": "disliked"},
            {"title": "Summer Games", "note": "duplicate"},
            {"title": "Bomb Mania", "players": "4", "minutes": 900, "note": "Simultaneous with a 4-player adapter."}]})
        _game(c, "Summer Games")
        cont.taste.rate("Paperboy", -1)
        assert c.post("/api/playlists", json={"prompt": "no"}).status_code == 422
        p = c.post("/api/playlists", json={"prompt": "4-player party pack for Friday"}).json()
        assert p["name"] == "Friday Frenzy" and [i["title"] for i in p["items"]] == ["Summer Games", "Bomb Mania"]
        assert p["items"][0]["gameId"] and p["items"][1]["minutes"] is None and "Summer Games" in m.calls[0][1]
        assert "Paperboy" in m.calls[0][1]                                     # told to avoid disliked games
        pid = p["id"]
        assert c.patch(f"/api/playlists/{pid}/items/0", json={"done": True}).json()["done"] == 1
        assert c.patch(f"/api/playlists/{pid}/items/1", json={"remove": True}).json()["count"] == 1
        lst = c.get("/api/playlists").json()
        assert lst["playlists"][0]["name"] == "Friday Frenzy" and lst["suggestions"]
        c.delete(f"/api/playlists/{pid}")
        assert c.get(f"/api/playlists/{pid}").status_code == 404


def test_weekly_recap(app_client):
    from app.models.db import TasteEvent
    with app_client() as c:
        cont = c.app.state.container
        assert c.get("/api/recap").json()["note"].startswith("No games played")
        bb = _game(c, "Bubble Bobble")
        cont.taste.record("play_browser", game_id=bb)
        cont.taste.record("session", game_id=bb, value=42)
        cont.taste.record("search", text="platform games")
        with cont.sf() as s:                                          # last month: not this week, and not "new"
            s.add(TasteEvent(kind="play", title="Elite", title_key=title_key("Elite"),
                             timestamp=datetime.now(UTC) - timedelta(days=30)))
            s.commit()
        m = _model(c, {"headline": "Bubble trouble!", "summary": "You blew a lot of bubbles. Keep it up.",
                       "challenges": [{"game": "Bubble Bobble", "challenge": "Reach round 20", "tip": "Stay low"}]})
        r = c.post("/api/recap/refresh").json()
        st = r["stats"]
        assert st["minutes"] == 42 and st["plays"] == 1 and st["searches"] == 1 and st["newGames"] == ["Bubble Bobble"]
        assert [g["title"] for g in st["games"]] == ["Bubble Bobble"]
        assert r["headline"] == "Bubble trouble!" and r["challenges"][0]["gameId"] == bb and "42 minutes" in m.calls[0][1]
        assert c.post("/api/recap/challenges/0", json={"done": True}).json()["challenges"][0]["done"] is True
        assert c.get("/api/recap").json()["challenges"][0]["done"] is True     # cached with the tick
        assert c.post("/api/recap/challenges/5", json={"done": True}).status_code == 404


def test_enrich_job_runs(app_client, monkeypatch):
    import time as _time

    from app.services import enrich as enrich_mod
    monkeypatch.setattr(enrich_mod, "PAUSE", 0)
    with app_client() as c:
        _model(c, {"genre": "Sport", "tags": ["party"], "description": "Olympics."})
        gid = _game(c, "Summer Games")
        c.post("/api/library/details")
        for _ in range(100):
            if not c.get("/api/library/details").json()["running"]:
                break
            _time.sleep(0.02)
        job = c.get("/api/library/details").json()
        assert job["done"] == 1 and job["filled"] == 1 and job["errors"] == 0
        assert c.get(f"/api/games/{gid}").json()["genre"] == "Sport"



def test_playlist_player_count_is_enforced(app_client):
    with app_client() as c:
        _model(c, {"name": "Party", "items": [
            {"title": "Bubble Bobble", "players": "2", "note": "co-op"},
            {"title": "M.U.L.E.", "players": "1-4", "note": "4 players"},
            {"title": "Unknown Count", "note": "no player info"}]})
        p = c.post("/api/playlists", json={"prompt": "a four player party"}).json()
        assert [i["title"] for i in p["items"]] == ["M.U.L.E.", "Unknown Count"]   # 2-player dropped
        p = c.post("/api/playlists", json={"prompt": "30-minute lunch break"}).json()
        assert len(p["items"]) == 3                                                # no player count asked
