import json
from datetime import UTC, datetime, timedelta

from test_ask import FakeModel

from app.library.titles import title_key


def _game(c, title, genre=None, publisher=None, year=None, played=0):
    from app.models.db import Game
    with c.app.state.container.sf() as s:
        g = Game(group_key=f"t:{title}", title=title, normalized_title=title_key(title), format="d64", genre=genre,
                 publisher=publisher, year=year, play_count=played)
        s.add(g)
        s.commit()
        return g.id


def test_profile_weighs_signals_and_decays(app_client):
    from app.models.db import TasteEvent
    with app_client() as c:
        t = c.app.state.container.taste
        bb = _game(c, "Bubble Bobble", genre="Platform", publisher="Firebird", year=1987)
        t.record("play", game_id=bb)
        t.record("session", game_id=bb, value=40)
        t.record("search", text="platform games")
        t.record("search", text="Platform games")          # same search twice → listed once
        t.rate("Bubble Bobble", 1, bb)
        t.rate("Paperboy", -1)
        t.record("play", title="Summer Games")
        with c.app.state.container.sf() as s:              # an old play counts much less
            s.add(TasteEvent(kind="play", title="Old Game", title_key=title_key("Old Game"),
                             timestamp=datetime.now(UTC) - timedelta(days=400)))
            s.commit()
        p = t.profile()
        titles = [x["title"] for x in p["liked"]]
        assert titles[0] == "Bubble Bobble" and "Summer Games" in titles and "Old Game" not in titles
        assert p["liked"][0]["why"] == ["👍", "played 1×", "40 min"]
        assert p["disliked"] == ["Paperboy"] and p["searches"] == ["Platform games"]
        assert p["genres"] == ["Platform"] and p["publishers"] == ["Firebird"] and p["decades"] == ["1980s"]
        t.rate("Summer Games", -1)                         # 👎 moves a game out of "liked"
        assert "Summer Games" not in [x["title"] for x in t.profile()["liked"]]


def test_ai_recommendations_are_filtered_resolved_and_cached(app_client, monkeypatch):
    reply = {"summary": "Loves cute arcade platformers.", "picks": [
        {"title": "Bubble Bobble", "reason": "already liked", "because": []},                 # liked → dropped
        {"title": "Paperboy", "reason": "disliked", "because": []},                          # disliked → dropped
        {"title": "Basketball", "reason": "already played", "because": []},                 # played → dropped
        {"title": "The Great Giana Sisters", "reason": "Cute platforming like Bubble Bobble.",
         "because": ["Bubble Bobble", "Not Liked"], "kind": "match"},
        {"title": "Rainbow Islands", "reason": "The Bubble Bobble sequel.", "because": ["bubble bobble"], "kind": "gem"},
        {"title": "Rainbow Islands", "reason": "duplicate"},
        {"title": "Uridium", "reason": "Something different.", "kind": "weird"}]}
    with app_client() as c:
        cont = c.app.state.container
        cont.provider = FakeModel(cont.settings, json.dumps(reply))
        bb = _game(c, "Bubble Bobble")
        _game(c, "Basketball", played=2)
        giana = _game(c, "The Great Giana Sisters")
        cont.taste.rate("Bubble Bobble", 1, bb)
        cont.taste.rate("Paperboy", -1)
        assert c.get("/api/recommendations").json()["stale"] is True   # nothing generated yet
        r = c.post("/api/recommendations/refresh").json()
        assert [p["title"] for p in r["picks"]] == ["The Great Giana Sisters", "Rainbow Islands", "Uridium"]
        g = r["picks"][0]
        assert g["gameId"] == giana and g["because"] == ["Bubble Bobble"] and g["rating"] == 0
        assert r["picks"][1]["because"] == ["Bubble Bobble"] and r["picks"][2]["kind"] == "match"
        assert r["summary"] == "Loves cute arcade platformers." and r["ai"] is True and r["stale"] is False
        system, user, _ = cont.provider.calls[0]
        assert "Bubble Bobble (👍)" in user and "Disliked: Paperboy" in user and "Exclude (already played): Basketball" in user
        assert r["again"] and r["again"][0]["title"] == "Bubble Bobble"           # liked, not played lately
        # 👎 on a pick hides it at once and marks the list stale (the profile changed)
        c.post("/api/taste/rate", json={"title": "Uridium", "value": -1})
        cur = c.get("/api/recommendations").json()
        assert "Uridium" not in [p["title"] for p in cur["picks"]] and cur["stale"] is True


def test_new_player_gets_starter_picks(app_client):
    with app_client() as c:
        cont = c.app.state.container
        cont.provider = FakeModel(cont.settings, json.dumps({"picks": [{"title": "Boulder Dash", "reason": "A classic."}]}))
        r = c.post("/api/recommendations/refresh").json()
        assert r["picks"][0]["title"] == "Boulder Dash"
        assert "new" in cont.provider.calls[0][1]


def test_library_fallback_without_ai(app_client):
    with app_client() as c:
        cont = c.app.state.container
        bb = _game(c, "Bubble Bobble", genre="Platform", publisher="Firebird", year=1987)
        _game(c, "Winter Games", genre="Sport")
        _game(c, "Giana Sisters", genre="Platform")
        _game(c, "Elite", publisher="Firebird")
        _game(c, "Played Already", genre="Platform", played=1)
        cont.taste.rate("Bubble Bobble", 1, bb)
        r = c.post("/api/recommendations/refresh").json()
        titles = [p["title"] for p in r["picks"]]
        assert titles[:2] == ["Giana Sisters", "Elite"] and "Played Already" not in titles and "Bubble Bobble" not in titles
        assert r["picks"][0]["reason"] == "Another platform game, like Bubble Bobble." and r["ai"] is False
        assert "AI model" in r["note"]


def test_actions_record_taste_and_can_be_forgotten(app_client):
    with app_client() as c:
        gid = _game(c, "Bruce Lee")
        c.post("/api/command", json={"text": "what are the best C64 racing games?"})
        c.post(f"/api/games/{gid}/favorite")
        c.post("/api/taste/event", json={"kind": "play_browser", "gameId": gid})
        c.post("/api/taste/event", json={"kind": "session", "gameId": gid, "minutes": 12})
        c.post("/api/taste/event", json={"kind": "session", "gameId": gid, "minutes": 0.2})   # too short → ignored
        assert c.post("/api/taste/event", json={"kind": "delete_everything", "gameId": gid}).status_code == 422
        p = c.get("/api/taste/profile").json()
        assert p["liked"][0]["title"] == "Bruce Lee" and p["liked"][0]["why"] == ["played 1×", "12 min", "favorite"]
        assert "what are the best C64 racing games?" in p["searches"]
        assert p["events"] == 4
        assert c.get("/api/taste/rating", params={"title": "Bruce Lee"}).json()["value"] == 0
        c.delete("/api/taste/game", params={"title": "Bruce Lee"})
        assert c.get("/api/taste/profile").json()["liked"] == []
        c.delete("/api/taste")
        assert c.get("/api/taste/profile").json()["events"] == 0
