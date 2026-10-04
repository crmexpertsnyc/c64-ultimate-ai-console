import asyncio
import json

from test_ask import FakeModel

from app.library.titles import title_key
from app.services.achievements import read_metrics
from app.services.screen_reader import ScreenWatcher


def _game(c, title):
    from app.models.db import Game
    with c.app.state.container.sf() as s:
        g = Game(group_key=f"t:{title}", title=title, normalized_title=title_key(title), format="d64")
        s.add(g)
        s.commit()
        return g.id


def _sample(*rows, raw=None):
    return {"mode": "text", "rows": list(rows), "rawRows": raw or list(rows)}


def test_reading_scores_from_the_screen():
    want = {"score": 12340, "hiscore": 50000, "level": 5, "lives": 3}
    assert read_metrics(["SCORE 012340  HI 50000", "ROUND 05 LIVES 3"]) == want
    assert read_metrics(["HIGH SCORE 99999  SCORE 120"]) == {"hiscore": 99999, "score": 120}   # the record is not yours
    raw = ["  SCORE      LIVES" + " " * 22, "  004560     2" + " " * 26]
    assert read_metrics(["SCORE LIVES", "004560 2"], raw) == {"score": 4560, "lives": 2}        # numbers under labels
    assert read_metrics(["3 LIVES LEFT", "STAGE 2"]) == {"lives": 3, "level": 2}
    assert read_metrics(["**** COMMODORE 64 BASIC V2 ****", "64K RAM SYSTEM 38911 BASIC BYTES FREE"]) == {}


def test_scores_need_two_readings_and_unlock_achievements(app_client):
    with app_client() as c:
        gid = _game(c, "Bubble Bobble")
        me, her = {}, {"X-C64-Profile": str(c.post("/api/profiles", json={"name": "Maya"}).json()["id"])}
        first = c.post(f"/api/games/{gid}/progress", json={"sample": _sample("SCORE 1200", "ROUND 01")}, headers=me).json()
        assert first["metrics"] == {} and first["read"]["score"] == 1200            # seen once: not yet
        second = c.post(f"/api/games/{gid}/progress", json={"sample": _sample("SCORE 1200", "ROUND 01")}, headers=me).json()
        assert second["metrics"] == {"score": 1200, "level": 1} and second["newBest"] == 1200
        assert [a["title"] for a in second["unlocked"]] == ["Into the game"]
        # a glitchy reading between two good ones is ignored; the score keeps climbing in the same entry
        c.post(f"/api/games/{gid}/progress", json={"sample": _sample("SCORE 9999999", "ROUND 01")}, headers=me)
        for _ in range(2):
            c.post(f"/api/games/{gid}/progress", json={"sample": _sample("SCORE 4500", "ROUND 02")}, headers=me)
        # not counted while the analyzer says the game is not being played (attract mode / demo)
        for _ in range(2):
            c.post(f"/api/games/{gid}/progress", json={"sample": _sample("SCORE 88000"), "playing": False}, headers=me)
        for _ in range(2):
            c.post(f"/api/games/{gid}/progress", json={"sample": _sample("SCORE 3000")}, headers=her)
        board = c.get(f"/api/games/{gid}/achievements", headers=me).json()
        assert [(x["name"], x["score"]) for x in board["scores"]] == [("Player 1", 4500), ("Maya", 3000)]
        into = next(a for a in board["achievements"] if a["title"] == "Into the game")
        assert into["unlocked"] and {u["name"] for u in into["unlockedBy"]} == {"Player 1", "Maya"}
        assert board["seen"]["score"] >= 4500 and not board["hasGameAchievements"]
        # 🤖 AI drafts game achievements from what the game shows; unusable metrics / targets are dropped
        cont = c.app.state.container
        cont.provider = FakeModel(cont.settings, json.dumps({"achievements": [
            {"title": "Bubble rookie", "description": "Score 5,000 points.", "icon": "🫧", "metric": "score", "target": 5000},
            {"title": "Round three", "description": "Reach round 3.", "icon": "3️⃣", "metric": "level", "target": 3},
            {"title": "Speedy", "description": "Beat the clock.", "metric": "speed", "target": 10},
            {"title": "Impossible", "description": "x", "metric": "score", "target": 10 ** 12}]}))
        made = c.post(f"/api/games/{gid}/achievements/generate").json()["achievements"]
        assert [a["title"] for a in made] == ["Bubble rookie", "Round three"]
        assert "score up to" in cont.provider.calls[0][1]
        for _ in range(2):
            got = c.post(f"/api/games/{gid}/progress", json={"sample": _sample("SCORE 5200", "ROUND 03")}, headers=me).json()
        board = c.get(f"/api/games/{gid}/achievements", headers=me).json()
        assert {a["title"] for a in board["achievements"] if a["unlocked"]} >= {"Bubble rookie", "Round three"}
        assert any(r["title"] == "Bubble rookie" for r in c.get("/api/achievements/recent").json()["recent"])
        assert got["newBest"] == 5200


def test_generate_needs_something_on_screen(app_client):
    with app_client() as c:
        c.app.state.container.provider = FakeModel(c.app.state.container.settings, "{}")
        gid = _game(c, "Elite")
        r = c.post(f"/api/games/{gid}/achievements/generate")
        assert r.status_code == 409 and "score or round" in r.json()["detail"]


class FakeC64:
    """C64 memory for the screen reader (read-only, like the Ultimate's machine:readmem)."""

    def __init__(self, io_visible=True):
        self.mem = bytearray(65536)
        for base in (0x0400, 0x4800):          # screens are cleared with spaces, as the C64 does
            self.mem[base:base + 1000] = b" " * 1000
        if io_visible:
            self.mem[0xD011] = 0x1B          # text mode, display on
            self.mem[0xD018] = 0x14          # screen $0400, ROM charset (upper case)
            self.mem[0xDD00] = 0x97          # VIC bank 0
            self.mem[0xDD02] = 0x3F
        self.mem[0x0288] = 0x04
        self.mem[0x0314:0x0316] = bytes([0x31, 0xEA])
        self.reads = 0

    def put(self, row, col, text, base=0x0400):
        for i, ch in enumerate(text):
            o = ord(ch)
            self.mem[base + row * 40 + col + i] = o - 64 if 65 <= o <= 90 else o

    async def read_memory(self, address, length=256):
        self.reads += 1
        return bytes(self.mem[address:address + length])


def test_screen_watcher_reads_the_real_c64_layout():
    c64 = FakeC64()
    c64.put(0, 2, "SCORE 012340")
    c64.put(2, 2, "PRESS FIRE TO START")
    c64.mem[0x1000:0x1003] = bytes([0xAD, 0x00, 0xDC])   # LDA $DC00 — the program reads joystick port 2
    w = ScreenWatcher(c64)
    s1 = asyncio.run(w.sample(min_interval=0))
    s2 = asyncio.run(w.sample(min_interval=0))
    assert s1["mode"] == "text" and s1["base"] == 0x400 and s1["ioVisible"] and s1["kernalIrq"]
    assert s1["rows"][0] == "SCORE 012340" and s1["rawRows"][0].startswith("  SCORE 012340")
    assert "PRESS FIRE TO START" in s2["staticText"] and s2["ports"]["dc00Reads"] == 1 and s2["source"] == "c64"
    # several viewers within the interval share one reading
    n = c64.reads
    asyncio.run(w.sample(min_interval=10))
    assert c64.reads == n
    # VIC in bank 1 with the screen at $0800 of that bank → $4800
    c64.mem[0xDD00] = 0x96
    c64.mem[0xD018] = 0x24
    c64.put(5, 0, "ROUND 07", base=0x4800)
    s3 = asyncio.run(w.sample(min_interval=0))
    assert s3["base"] == 0x4800 and s3["rows"][5] == "ROUND 07"
    # bitmap mode
    c64.mem[0xD011] = 0x3B
    assert asyncio.run(w.sample(min_interval=0))["mode"] == "bitmap"


def test_screen_watcher_falls_back_when_io_is_hidden():
    c64 = FakeC64(io_visible=False)
    c64.put(1, 0, "READY.")
    s = asyncio.run(ScreenWatcher(c64).sample(min_interval=0))
    assert s["ioVisible"] is False and s["base"] == 0x400 and s["rows"][1] == "READY."
