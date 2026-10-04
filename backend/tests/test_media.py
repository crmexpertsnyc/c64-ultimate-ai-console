import time

import pytest
from helpers import make_d64

from app.services.live import find_ffmpeg


def _scan(c, lib):
    c.post("/api/library/scan", json={"paths": [str(lib)]})
    for _ in range(100):
        if not c.get("/api/library/scan").json()["running"]:
            return
        time.sleep(0.05)


def test_screenshot_capture_gallery_cover_and_delete(app_client):
    with app_client() as c:
        shot = c.post("/api/screenshots", json={"title": "Test Shot"}).json()
        assert shot["name"].endswith("-test-shot.png")
        img = c.get(shot["url"])
        assert img.status_code == 200 and img.content[:8] == b"\x89PNG\r\n\x1a\n"
        assert [s["name"] for s in c.get("/api/screenshots").json()] == [shot["name"]]
        assert c.get("/api/screenshots/..%2Fsettings.json").status_code == 404


def test_cover_from_screenshot_and_cleanup_on_delete(app_client, tmp_path):
    lib = tmp_path / "g"
    lib.mkdir()
    (lib / "Bruce Lee.d64").write_bytes(make_d64())
    with app_client(AUTO_COVER_ART=False) as c:
        _scan(c, lib)
        game = c.get("/api/library").json()["items"][0]
        shot = c.post("/api/screenshots", json={"game_id": game["id"], "title": game["title"]}).json()
        r = c.post(f"/api/games/{game['id']}/cover", json={"screenshot": shot["name"]}).json()
        assert r["coverUrl"] == shot["url"]
        c.delete(f"/api/screenshots/{shot['name']}")
        assert c.get(f"/api/games/{game['id']}").json()["coverUrl"] is None


def _colourful_png() -> bytes:
    import io

    from PIL import Image, ImageDraw
    img = Image.new("RGB", (768, 544), (0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, col in enumerate([(200, 40, 40), (40, 200, 40), (240, 220, 60), (60, 120, 230), (230, 130, 40), (200, 60, 200)]):
        d.rectangle([80 + i * 100, 100, 150 + i * 100, 450], fill=col)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_quality_rejects_basic_screen_accepts_picture():
    from app.services.screenshots import png_quality
    from app.ultimate.simulator import SimulatedUltimate
    from app.ultimate.streams import render_text_frame
    basic = render_text_frame(SimulatedUltimate().lines, "", "PNG")
    assert png_quality(basic)["ok"] is False
    assert png_quality(_colourful_png())["ok"] is True


def test_auto_cover_keeps_best_in_game_frame(app_client, tmp_path):
    lib = tmp_path / "g"
    lib.mkdir()
    (lib / "Boulder Dash.prg").write_bytes(b"\x01\x08" + b"\xea" * 8)
    with app_client(AUTO_COVER_DELAY_SECONDS=0) as c:
        container = c.app.state.container
        good = _colourful_png()

        async def fake_best(window=0, interval=0, still_wanted=None):  # noqa: ARG001
            from app.services.screenshots import png_quality
            return good, png_quality(good)

        container.screenshots.best_frame = fake_best
        _scan(c, lib)
        game = c.get("/api/library").json()["items"][0]
        c.post(f"/api/games/{game['id']}/play", json={})
        for _ in range(60):
            cover = c.get(f"/api/games/{game['id']}").json()["coverUrl"]
            if cover:
                break
            time.sleep(0.05)
        assert cover and cover.startswith("/api/screenshots/")
        assert c.get(cover).content == good


def test_box_art_wins_and_user_choice_is_kept(app_client, tmp_path):
    lib = tmp_path / "g"
    lib.mkdir()
    (lib / "Bruce Lee.d64").write_bytes(make_d64())
    with app_client(AUTO_COVER_ART=False, COVER_ART_ONLINE=True) as c:
        container = c.app.state.container
        art = container.boxart.folder / "libretro-boxart-bruce-lee-usa-europe.png"
        art.write_bytes(_colourful_png())

        async def fake_art_for(title, alternates=None):  # noqa: ARG001
            return ("boxart", art) if title == "Bruce Lee" else None

        container.boxart.art_for = fake_art_for
        _scan(c, lib)
        game = c.get("/api/library").json()["items"][0]
        for _ in range(60):  # a scan triggers the cover search in the background
            g = c.get(f"/api/games/{game['id']}").json()
            if g["coverUrl"]:
                break
            time.sleep(0.05)
        assert g["coverUrl"] == "/api/art/file/libretro-boxart-bruce-lee-usa-europe.png"
        assert c.get(g["coverUrl"]).status_code == 200
        # The user picks a screenshot: a later refresh must not replace it.
        shot = c.post("/api/screenshots", json={"game_id": game["id"], "title": "Bruce Lee"}).json()
        c.post(f"/api/games/{game['id']}/cover", json={"screenshot": shot["name"]})
        c.post("/api/library/covers", json={})
        time.sleep(0.3)
        assert c.get(f"/api/games/{game['id']}").json()["coverUrl"] == shot["url"]


def test_viewer_counting_starts_and_stops_streams(app_client):
    import asyncio
    with app_client() as c:
        dev = c.app.state.container.device
        dev.STREAM_LINGER_SECONDS = 0.01
        portal = c.portal
        portal.call(dev.join_stream, "video")
        portal.call(dev.join_stream, "video")
        assert "video" in dev.streams.active and dev.stream_viewers()["video"] == 2
        portal.call(dev.leave_stream, "video")
        assert "video" in dev.streams.active
        portal.call(dev.leave_stream, "video")
        portal.call(asyncio.sleep, 0.1)
        assert "video" not in dev.streams.active


@pytest.mark.skipif(not find_ffmpeg(), reason="ffmpeg not available")
def test_record_mp4_in_simulation(app_client):
    with app_client() as c:
        st = c.post("/api/live/record/start").json()
        assert st["running"] and st["mode"] == "record"
        time.sleep(2.5)
        c.post("/api/live/stop")
        recs = c.get("/api/recordings").json()
        assert len(recs) == 1 and recs[0]["size"] > 1000
        mp4 = c.get(recs[0]["url"]).content
        assert b"ftyp" in mp4[:32]


def test_live_requires_key_and_hides_it(app_client):
    with app_client() as c:
        r = c.post("/api/live/live/start")
        assert r.status_code == 409 and "stream key" in r.json()["detail"]
        c.put("/api/settings", json={"LIVE_RTMP_URL": "rtmp://example.invalid/app", "LIVE_STREAM_KEY": "live_SECRET_123"})
        st = c.get("/api/live").json()
        assert st["streamKeySet"] is True and "live_SECRET_123" not in str(st)
        assert c.get("/api/settings").json()["LIVE_STREAM_KEY"] == ""
