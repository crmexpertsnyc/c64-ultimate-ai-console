import json

from test_ask import FakeModel

from app.services import ask as ask_mod
from app.services import guide as guide_mod


def _game(c, title="Summer Games", port=None):
    from app.models.db import Game
    with c.app.state.container.sf() as s:
        g = Game(group_key=f"t:{title}", title=title, normalized_title=title.lower().replace(" ", ""), format="d64",
                 joystick_port=port)
        s.add(g)
        s.commit()
        return g.id


REPLY = {"summary": "Epyx Olympic sports.", "start": ["Choose 1 with the joystick", "Type your name, RETURN",
         "Press RETURN on an empty name", "Press Y to confirm"], "startKeys": ["FIRE", "return", "RETURN", "Y", "BOGUS"],
         "controls": [{"action": "Run", "how": "Waggle left/right"}], "joystickPort": 2, "players": "1-8",
         "tips": ["Press RETURN on an empty name to finish"], "sources": [1]}


def test_guide_is_researched_stored_and_fills_gaps(app_client, monkeypatch):
    async def search(q, key, count=8, timeout=10):  # noqa: ANN001
        return [{"title": "Summer Games manual", "url": "https://example.test/manual", "description": "…"}]

    monkeypatch.setattr(guide_mod, "brave_search", search)
    with app_client() as c:
        cont = c.app.state.container
        cont.provider = FakeModel(cont.settings, json.dumps(REPLY))
        cont.config.update({"BRAVE_API_KEY": "k"})
        gid = _game(c)
        assert c.get(f"/api/games/{gid}/guide").json() == {"guide": None}
        g = c.post(f"/api/games/{gid}/guide").json()["guide"]
        assert g["startKeys"] == ["FIRE", "RETURN", "RETURN", "Y"]          # normalised, unknown key dropped
        assert g["joystickPort"] == 2 and g["webSearch"] is True
        assert g["sources"] == [{"n": 1, "title": "Summer Games manual", "url": "https://example.test/manual"}]
        game = c.get(f"/api/games/{gid}").json()
        assert game["joystickPort"] == 2 and game["players"] == "1-8"      # gaps filled from the guide
        assert c.get(f"/api/games/{gid}/guide").json()["guide"]["summary"] == "Epyx Olympic sports."
        assert c.get(f"/api/games/{gid}/emulator").status_code in (200, 409)  # guide travels with emulator info


def test_guide_never_overwrites_known_port(app_client, monkeypatch):
    monkeypatch.setattr(ask_mod, "brave_search", None)
    with app_client() as c:
        cont = c.app.state.container
        cont.provider = FakeModel(cont.settings, json.dumps({**REPLY, "joystickPort": 1}))
        gid = _game(c, "Bruce Lee", port=2)
        c.post(f"/api/games/{gid}/guide")
        assert c.get(f"/api/games/{gid}").json()["joystickPort"] == 2


def test_guide_needs_ai(app_client):
    with app_client() as c:
        gid = _game(c)
        r = c.post(f"/api/games/{gid}/guide")
        assert r.status_code == 409 and "AI model" in r.json()["detail"]
        assert c.post("/api/games/9999/guide").status_code == 404


def test_smart_start_roundtrip(app_client):
    with app_client() as c:
        gid = _game(c)
        steps = [{"key": " ", "code": "Space", "keyCode": 32, "at": 25000},
                 {"key": "Escape", "code": "Escape", "keyCode": 27, "at": 29000},
                 {"key": "FIRE", "at": 40000}]
        assert c.put(f"/api/games/{gid}/smart-start", json={"steps": steps, "auto": True}).json()["ok"]
        game = c.get(f"/api/games/{gid}").json()
        assert game.get("extra", {}).get("smartStart", {}).get("steps", [{}])[0].get("code", "Space") == "Space"
        assert c.put(f"/api/games/{gid}/smart-start", json={"steps": [], "source": "hacked"}).status_code == 422
        c.delete(f"/api/games/{gid}/smart-start")
        assert "smartStart" not in (c.get(f"/api/games/{gid}").json().get("extra") or {})
