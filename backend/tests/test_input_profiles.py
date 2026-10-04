import json

from app.services import input_profiles as ip


def _game(c, tmp_path, title="Bubble Bobble", data=b"disk-image-1", port=None, guide=None):
    from app.models.db import Game, Media
    f = tmp_path / f"{title}.d64"
    f.write_bytes(data)
    with c.app.state.container.sf() as s:
        g = Game(group_key=f"t:{title}", title=title, normalized_title=title.lower().replace(" ", ""), format="d64",
                 joystick_port=port, extra={"guide": guide} if guide else {})
        g.media.append(Media(path=str(f), format="d64", size=len(data), mtime=f.stat().st_mtime))
        s.add(g)
        s.commit()
        return g.id, f


def test_profile_layers_and_learning_are_tied_to_the_file(app_client, tmp_path):
    guide = {"startKeys": ["SPACE", "FIRE"], "joystickPort": 1, "controls": [{"action": "Jump", "how": "Up"}]}
    with app_client() as c:
        gid, f = _game(c, tmp_path, guide=guide)
        p = c.get(f"/api/games/{gid}/input-profile").json()
        assert p["startupSource"] == "guide" and [s["key"] for s in p["startupSequence"]] == ["SPACE", "FIRE"]
        assert p["joystickPort"] == 1 and p["joystickPortSource"] == "guide" and p["controls"] == {"Jump": "Up"}
        sha = p["sha256"]
        assert len(sha) == 64
        steps = [{"key": "space", "when": "PROUDLY PRESENTS"}, {"key": "RUN/STOP", "when": "SPACE TO READ OR RUN/STOP TO START"},
                 {"key": "rm -rf", "when": "x"}, {"key": "FIRE+SPACE", "when": "(picture)"}]
        r = c.put(f"/api/games/{gid}/input-profile/learned", json={"sha256": sha, "startupSequence": steps, "joystickPort": 2})
        assert r.status_code == 200
        p = r.json()
        assert p["startupSource"] == "learned" and p["joystickPortSource"] == "learned" and p["joystickPort"] == 2
        assert [s["key"] for s in p["startupSequence"]] == ["SPACE", "RUN/STOP", "FIRE+SPACE"]   # unknown key dropped
        # Emulator info carries the profile too.
        assert c.get(f"/api/games/{gid}/emulator").json()["inputProfile"]["startupSource"] == "learned"
        # Learning for another file (another release) is refused …
        bad = c.put(f"/api/games/{gid}/input-profile/learned", json={"sha256": "0" * 64, "joystickPort": 1})
        assert bad.status_code == 409
        # … and when the file changes, what was learned no longer applies.
        f.write_bytes(b"another release of the game")
        from app.models.db import Media
        with c.app.state.container.sf() as s:
            m = s.query(Media).filter_by(game_id=gid).one()
            m.size, m.mtime = f.stat().st_size, f.stat().st_mtime + 5
            s.commit()
        p = c.get(f"/api/games/{gid}/input-profile").json()
        assert p["sha256"] != sha and p["startupSource"] == "guide"
        assert c.delete(f"/api/games/{gid}/input-profile/learned").json()["ok"]
        assert c.get("/api/games/9999/input-profile").status_code == 404


def test_builtin_profiles_match_by_hash_or_title(app_client, tmp_path, monkeypatch):
    import hashlib
    data = b"known-release"
    builtin = [{"game": "Bubble Bobble", "sha256": [hashlib.sha256(data).hexdigest()], "startupSequence": [{"key": "RUN/STOP"}],
                "joystickPort": 2, "controls": {"Fire": "Blow bubble"}},
               {"game": "Some Game", "aliases": ["Some Game Deluxe"], "startupSequence": [{"key": "F1"}], "joystickPort": 1}]
    monkeypatch.setattr(ip, "_load_builtin", lambda: builtin)
    with app_client() as c:
        c.app.state.container.input_profiles = ip.InputProfiles(c.app.state.container.sf)
        gid, _ = _game(c, tmp_path, data=data)
        p = c.get(f"/api/games/{gid}/input-profile").json()
        assert p["startupSource"] == "builtin" and p["startupSequence"] == [{"key": "RUN/STOP"}]
        assert p["controls"] == {"Fire": "Blow bubble"}
        gid2, _ = _game(c, tmp_path, title="Some Game Deluxe", data=b"other")
        p = c.get(f"/api/games/{gid2}/input-profile").json()
        # A title match gives the port, never start keys (those depend on the crack / trainer in front).
        assert p["joystickPort"] == 1 and p["joystickPortSource"] == "builtin" and p["startupSequence"] == []


def test_builtin_file_is_valid_json():
    data = json.loads(ip.BUILTIN_PATH.read_text(encoding="utf-8"))
    for prof in data["profiles"]:
        assert prof["game"] and all(s["key"] in ip.PROFILE_KEYS for s in prof.get("startupSequence", []))
