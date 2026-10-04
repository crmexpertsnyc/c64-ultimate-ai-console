import json
import sqlite3

from test_ask import FakeModel

from app.library.titles import title_key


def _game(c, title):
    from app.models.db import Game
    with c.app.state.container.sf() as s:
        g = Game(group_key=f"t:{title}", title=title, normalized_title=title_key(title), format="d64")
        s.add(g)
        s.commit()
        return g.id


def test_profiles_keep_taste_ratings_and_saves_apart(app_client):
    with app_client() as c:
        r = c.get("/api/profiles").json()
        assert r["profiles"][0]["id"] == 1 and r["current"] == 1 and r["emojis"]
        kid = c.post("/api/profiles", json={"name": "Maya", "emoji": "🦄", "kids": True}).json()
        assert kid["kids"] is True and c.post("/api/profiles", json={"name": "Maya"}).status_code == 400
        me, her = {}, {"X-C64-Profile": str(kid["id"])}
        assert c.get("/api/profiles", headers=her).json()["current"] == kid["id"]
        assert c.get("/api/profiles", headers={"X-C64-Profile": "999"}).json()["current"] == 1   # unknown → default
        gid = _game(c, "Bubble Bobble")
        c.post("/api/taste/rate", json={"title": "Bubble Bobble", "value": 1}, headers=me)
        c.post("/api/taste/rate", json={"title": "Bubble Bobble", "value": -1}, headers=her)   # same game, other person
        c.post("/api/taste/event", json={"kind": "session", "gameId": gid, "minutes": 20}, headers=her)
        mine = c.get("/api/taste/profile", headers=me).json()
        hers = c.get("/api/taste/profile", headers=her).json()
        assert [x["title"] for x in mine["liked"]] == ["Bubble Bobble"] and mine["disliked"] == []
        assert hers["disliked"] == ["Bubble Bobble"] and hers["events"] == 1 and mine["events"] == 0
        # ?profile= works where a header can't be sent (sendBeacon)
        c.post(f"/api/taste/event?profile={kid['id']}", json={"kind": "play_browser", "gameId": gid})
        assert c.get("/api/taste/profile", headers=her).json()["events"] == 2
        # saves
        c.put(f"/api/games/{gid}/save", content=b"STATE-ME", headers=me)
        assert c.get(f"/api/games/{gid}/save/info", headers=her).json() == {"exists": False}
        c.put(f"/api/games/{gid}/save", content=b"STATE-HER", headers=her)
        assert c.get(f"/api/games/{gid}/save", headers=her).content == b"STATE-HER"
        assert c.get(f"/api/games/{gid}/save", headers=me).content == b"STATE-ME"
        assert [x["title"] for x in c.get("/api/saves", headers=her).json()["saves"]] == ["Bubble Bobble"]
        # a child's profile asks for family-friendly picks only
        cont = c.app.state.container
        cont.provider = FakeModel(cont.settings, json.dumps({"picks": [{"title": "Boulder Dash", "reason": "fun"}]}))
        c.post("/api/recommendations/refresh", headers=her)
        assert "family-friendly" in cont.provider.calls[-1][0]
        c.post("/api/recommendations/refresh", headers=me)
        assert "family-friendly" not in cont.provider.calls[-1][0]
        # edit / delete
        assert c.patch(f"/api/profiles/{kid['id']}", json={"name": "Maya B", "color": "red"}).json()["color"] == "#7c70da"
        assert c.delete("/api/profiles/1").status_code == 400
        c.delete(f"/api/profiles/{kid['id']}")
        assert [p["id"] for p in c.get("/api/profiles").json()["profiles"]] == [1]
        assert c.get("/api/taste/profile", headers=her).json()["events"] == 0   # falls back to profile 1 (no events)


def test_old_database_is_upgraded(tmp_path):
    from app.models.db import init_db
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)  # the tables as the previous version made them
    con.executescript("""
        CREATE TABLE ratings (id INTEGER PRIMARY KEY, title_key VARCHAR(255) NOT NULL, title VARCHAR(255) NOT NULL,
                              game_id INTEGER, value INTEGER NOT NULL, updated_at DATETIME);
        CREATE UNIQUE INDEX ix_ratings_title_key ON ratings (title_key);
        CREATE TABLE taste_events (id INTEGER PRIMARY KEY, timestamp DATETIME, kind VARCHAR(20) NOT NULL, game_id INTEGER,
                                   title VARCHAR(255), title_key VARCHAR(255), text VARCHAR(300), value FLOAT);
        INSERT INTO ratings (title_key, title, value) VALUES ('bubblebobble', 'Bubble Bobble', 1);
        INSERT INTO taste_events (kind, title, title_key, value) VALUES ('play', 'Elite', 'elite', 1);
    """)
    con.commit()
    con.close()
    init_db(f"sqlite:///{db}")
    init_db(f"sqlite:///{db}")  # idempotent
    con = sqlite3.connect(db)
    assert con.execute("select profile_id from ratings").fetchone() == (1,)
    assert con.execute("select profile_id from taste_events").fetchone() == (1,)
    con.execute("insert into ratings (profile_id, title_key, title, value) values (2, 'bubblebobble', 'Bubble Bobble', -1)")
    try:
        con.execute("insert into ratings (profile_id, title_key, title, value) values (2, 'bubblebobble', 'x', 1)")
        raise AssertionError("duplicate rating for the same profile was allowed")
    except sqlite3.IntegrityError:
        pass
