import base64
import json

from test_ask import FakeModel

from app.library.titles import title_key

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\0" * 50).decode()


def _game(c, title, tmp_path, fmt="d64", disks=1):
    from app.models.db import Game, Media
    f = tmp_path / f"{title}.{fmt}"
    f.write_bytes(b"image-" + title.encode())
    with c.app.state.container.sf() as s:
        g = Game(group_key=f"t:{title}", title=title, normalized_title=title_key(title), format=fmt, num_disks=disks)
        g.media.append(Media(path=str(f), format=fmt, size=f.stat().st_size, mtime=f.stat().st_mtime))
        s.add(g)
        s.commit()
        return g.id


def test_reports_are_logged_deduplicated_and_managed(app_client, tmp_path):
    with app_client() as c:
        gid = _game(c, "The Last Ninja", tmp_path, disks=2)
        diag = {"secondsSinceStart": 95, "fps": 50, "screenMode": "blank", "errors": ["drive: sector not found"],
                "screenshot": "ignored", "userAgent": "Chrome"}
        a = c.post("/api/issues", json={"gameId": gid, "category": "hangs", "source": "auto", "diagnostics": diag}).json()
        assert a["title"] == "The Last Ninja" and a["fileName"] == "The Last Ninja.d64" and len(a["sha256"]) == 64
        assert "screenshot" not in a["diagnostics"] and a["diagnostics"]["format"] == "d64"
        causes = " ".join(a["likelyCauses"])
        assert "fast loader" in causes and "disk 2" in causes and "sector not found" in causes
        # the same automatic problem again → one entry, counted; the 🛟 warning counter goes up too
        b = c.post("/api/issues", json={"gameId": gid, "category": "hangs", "source": "auto", "diagnostics": diag}).json()
        assert b["id"] == a["id"] and b["occurrences"] == 2
        assert c.get(f"/api/games/{gid}/emulator").json()["hangs"] == 2
        # a player's report is always its own entry, with a note and a screenshot
        u = c.post("/api/issues", json={"gameId": gid, "category": "controls", "note": "joystick does nothing",
                                        "screenshot": f"data:image/png;base64,{PNG}"}).json()
        assert u["id"] != a["id"] and u["source"] == "user" and u["hasScreenshot"]
        assert c.get(f"/api/issues/{u['id']}/screenshot").status_code == 200
        assert "other joystick port" in " ".join(u["likelyCauses"])
        # it worked later → noted on the open entries
        assert c.post(f"/api/games/{gid}/worked").json()["updated"] == 2
        assert c.get("/api/issues", params={"gameId": gid}).json()["issues"][0]["workedAt"]
        # manage
        f = c.patch(f"/api/issues/{a['id']}", json={"status": "fixed", "resolution": "Use the EasyFlash release"}).json()
        assert f["status"] == "fixed" and f["resolution"].startswith("Use")
        assert [i["id"] for i in c.get("/api/issues", params={"status": "active"}).json()["issues"]] == [u["id"]]
        csv_text = c.get("/api/issues/export.csv").text
        assert "The Last Ninja" in csv_text and "Hangs / freezes" in csv_text and "EasyFlash release" in csv_text
        # a new automatic hang after "fixed" opens a new entry (the fix did not hold)
        again = c.post("/api/issues", json={"gameId": gid, "category": "hangs", "source": "auto"}).json()
        assert again["id"] not in (a["id"], u["id"])
        c.delete(f"/api/issues/{u['id']}")
        assert c.get(f"/api/issues/{u['id']}/screenshot").status_code == 404


def test_investigate_with_ai(app_client, tmp_path):
    with app_client() as c:
        cont = c.app.state.container
        cont.provider = FakeModel(cont.settings, json.dumps({
            "summary": "The crack's fast loader is not emulated.", "likelyCause": "custom fast loader",
            "confidence": "medium", "steps": ["Try the EasyFlash release", "Play it on the real C64"]}))
        gid = _game(c, "Summer Games", tmp_path, fmt="g64")
        i = c.post("/api/issues", json={"gameId": gid, "category": "no_start", "source": "auto",
                                        "diagnostics": {"fps": 31}}).json()
        causes = " ".join(i["likelyCauses"])
        assert "copy-protected" in causes and "31 fps" in causes
        r = c.post(f"/api/issues/{i['id']}/investigate").json()
        assert r["analysis"]["likelyCause"] == "custom fast loader" and r["status"] == "investigating"
        assert "Rule-based likely causes" in cont.provider.calls[0][1]


def test_bad_reports_are_refused(app_client, tmp_path):
    with app_client() as c:
        assert c.post("/api/issues", json={"gameId": 999, "category": "hangs"}).status_code == 404
        gid = _game(c, "Elite", tmp_path)
        assert c.post("/api/issues", json={"gameId": gid, "category": "rm -rf"}).status_code == 400
        assert c.post("/api/issues", json={"gameId": gid, "category": "other", "source": "admin"}).status_code == 422
        assert c.patch("/api/issues/1", json={"status": "deleted"}).status_code == 422
        assert c.post("/api/issues/999/investigate").status_code == 404
