def test_browser_save_roundtrip(app_client):
    with app_client() as c:
        from app.models.db import Game
        with c.app.state.container.sf() as s:
            g = Game(group_key="test:save", title="Save Test", normalized_title="savetest", format="crt")
            s.add(g)
            s.commit()
            gid = g.id
        assert c.get(f"/api/games/{gid}/save/info").json() == {"exists": False}
        state = bytes(range(256)) * 40
        r = c.put(f"/api/games/{gid}/save", content=state, headers={"x-save-device": "iPhone", "x-save-kind": "auto"})
        assert r.json()["ok"]
        info = c.get(f"/api/games/{gid}/save/info").json()
        assert info["exists"] and info["size"] == len(state) and info["device"] == "iPhone" and info["kind"] == "auto"
        assert c.get(f"/api/games/{gid}/save").content == state
        assert c.put(f"/api/games/{gid}/save/thumb", content=b"not a png").status_code == 400
        assert c.put(f"/api/games/{gid}/save/thumb", content=b"\x89PNG\r\n\x1a\n" + bytes(20)).json()["ok"]
        assert c.get(f"/api/games/{gid}/save/info").json()["hasThumb"]
        c.delete(f"/api/games/{gid}/save")
        assert c.get(f"/api/games/{gid}/save/info").json() == {"exists": False}


def test_save_for_unknown_game(app_client):
    with app_client() as c:
        assert c.put("/api/games/99999/save", content=b"x").status_code == 404
        assert c.get("/api/games/99999/save").status_code == 404
