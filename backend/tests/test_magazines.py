import json
import re
import time
from urllib.parse import unquote

from test_ask import FakeModel

from app.models.db import Game
from app.services.magazines import (
    SERIES_BY_ID,
    MagIndex,
    chunk_text,
    details_url,
    fts_query,
    parse_multifile,
    parse_search,
    pick_text_file,
    reader_url,
    search_terms,
    snippet,
    words,
)

ZZAP_SEARCH = {"response": {"numFound": 5, "docs": [
    {"identifier": "zzap64-magazine-collection-1985-1993", "title": "Zzap!64 Collection", "date": "1985-01-01T00:00:00Z"},
    {"identifier": "zzap64-magazine-001", "title": "ZZap!64 Magazine Issue 001", "date": "1985-05-01T00:00:00Z"},
    {"identifier": "zzap64-magazine-002", "title": "ZZap!64 Magazine Issue 002", "date": "1985-06-01T00:00:00Z"},
    {"identifier": "zzap64-magazine--091", "title": "ZZap!64 Magazine Issue 091", "date": "1993-01-01T00:00:00Z"},
    {"identifier": "zzap64-magazine-003", "title": "ZZap!64 Magazine Issue 003", "date": "1985-07-01T00:00:00Z"},
]}}
CF_FILES = {"result": [
    {"name": "CommodoreFormat-001.pdf", "size": "30931690"},
    {"name": "CommodoreFormat-001_djvu.txt", "size": "314967"},
    {"name": "CommodoreFormat-002_djvu.txt", "size": "156127"},
    {"name": "CommodoreFormat-002.pdf", "size": "21956356"},
    {"name": "__ia_thumb.jpg", "size": "100"},
]}

FILLER = " ".join(f"word{i}" for i in range(400))
TEXTS = {
    "zzap64-magazine-001": FILLER + " PARADROID by Andrew Braybrook from Hewson. Transfer to a droid and clear the "
                                    "decks. Presentation 92% Graphics 87% Overall 93% A Gold Medal. " + FILLER,
    "zzap64-magazine-002": FILLER + " Uridium screams across the screen. Paradroid fans will love it. " + FILLER,
}


def _files(name, size):
    return json.dumps({"result": [{"name": name.replace("_djvu.txt", ".pdf"), "size": "999"},
                                  {"name": name, "size": str(size)}]})


def fake_fetch(calls, fail=()):
    async def fetch(url):
        calls.append(url)
        if any(f in url for f in fail):
            raise OSError("503 Service Unavailable")
        if "advancedsearch" in url:
            return json.dumps(ZZAP_SEARCH if "zzap64" in unquote(url) else {"response": {"docs": []}})
        if url.endswith("/metadata/commodore-format/files"):
            return json.dumps(CF_FILES)
        if "/metadata/" in url:
            ident = url.split("/metadata/")[1].split("/")[0]
            if ident == "zzap64-magazine-003":
                return _files("Z3_djvu.txt", 6_000_000)            # too large: skipped
            return _files(f"{ident}_djvu.txt", 1000)
        if "/download/" in url:
            ident = url.split("/download/")[1].split("/")[0]
            if ident == "commodore-format":
                return "Commodore Format reviews Mayhem in Monsterland 95%. " + FILLER
            return TEXTS.get(ident, "Nothing much here " + FILLER)
        raise AssertionError(url)
    return fetch


class ExcerptModel(FakeModel):
    """Replies with JSON made from the prompt (so it can cite the excerpt that holds the review)."""

    def __init__(self, settings, make):  # noqa: ANN001
        super().__init__(settings, "")
        self.make = make

    async def complete(self, system, user, max_tokens=None, timeout=None):  # noqa: ANN001
        self.calls.append((system, user, max_tokens))
        return json.dumps(self.make(user))


def review_n(user):
    """The number of the excerpt holding Zzap's Paradroid review."""
    for m in re.finditer(r"^\[(\d+)\] .*\n(.*)$", user, re.M):
        if "Overall 93%" in m.group(2):
            return int(m.group(1))
    raise AssertionError("review excerpt missing")


def _setup(c, calls, fail=()):
    svc = c.app.state.container.magazines
    svc._fetch = fake_fetch(calls, fail)
    svc.gap = 0
    return svc


def _wait(c, series):
    for _ in range(200):
        s = {x["id"]: x for x in c.get("/api/magazines").json()["series"]}[series]
        if not s["indexing"]:
            return s
        time.sleep(0.05)
    raise AssertionError("indexing did not finish")


def test_issue_lists_for_both_shapes():
    zz = parse_search(json.dumps(ZZAP_SEARCH), SERIES_BY_ID["zzap64"])
    assert [i["key"] for i in zz] == ["zzap64-magazine-001", "zzap64-magazine-002", "zzap64-magazine-003",
                                      "zzap64-magazine--091"]        # no collection; sorted by date
    assert zz[3]["number"] == 91 and zz[0]["date"] == "1985-05" and zz[0]["stem"] is None
    cf = parse_multifile(json.dumps(CF_FILES), SERIES_BY_ID["commodore-format"])
    assert [(i["key"], i["number"], i["date"], i["text_file"]) for i in cf] == [
        ("commodore-format/CommodoreFormat-001", 1, "1990-10", "CommodoreFormat-001_djvu.txt"),
        ("commodore-format/CommodoreFormat-002", 2, "1990-11", "CommodoreFormat-002_djvu.txt")]
    assert cf[0]["title"] == "Commodore Format Issue 001" and cf[0]["text_size"] == 314967
    assert reader_url("zzap64-magazine-001") == "https://archive.org/embed/zzap64-magazine-001"
    assert reader_url("commodore-format", "CommodoreFormat-001") == \
        "https://archive.org/embed/commodore-format/CommodoreFormat-001"
    assert details_url("commodore-format", "CommodoreFormat-001", "Mayhem in Monsterland") == \
        "https://archive.org/details/commodore-format/CommodoreFormat-001?q=Mayhem%20in%20Monsterland"
    assert pick_text_file(_files("ZZap_64_Issue_001_djvu.txt", 5)) == ("ZZap_64_Issue_001_djvu.txt", 5)
    assert pick_text_file(json.dumps({"result": [{"name": "x.pdf"}]})) is None


def test_chunks_overlap():
    text = " ".join(f"w{i:04d}" for i in range(1000))            # 5,999 characters
    chunks = chunk_text(text)
    assert 4 <= len(chunks) <= 6 and all(len(c) <= 1500 for c in chunks)
    assert chunks[0].split()[-1] in chunks[1]                      # overlap
    assert chunks[-1].endswith("w0999") and chunk_text("  \n ") == []


def test_safe_fts_query_and_snippet(tmp_path):
    nasty = 'para"droid* OR (NEAR AND -x:y ^ "unterminated \' ; DROP TABLE'
    ws = words(nasty)
    assert ws == ["para", "droid", "OR", "NEAR", "AND", "x", "y", "unterminated", "DROP", "TABLE"]
    assert fts_query(["a", "b"]) == '"a" "b"' and fts_query(["a", "b"], "any") == '"a" OR "b"'
    assert fts_query(["a", "b"], "phrase") == '"a b"' and fts_query([]) == ""
    idx = MagIndex(tmp_path / "m.db")
    idx.store("k1", "zzap64", ["Paradroid by Andrew Braybrook, Overall 93%", "Uridium OR NEAR (droid)"])
    for mode in ("all", "any", "phrase"):
        idx.search(ws, mode)                                       # never an FTS syntax error
        idx.search(words('"*:()^-'), mode)
    assert [r[:2] for r in idx.search(words("paradroid"))] == [("k1", 0)]
    assert [r[:2] for r in idx.search(words("andrew braybrook"), "phrase")] == [("k1", 0)]
    assert idx.search(words("braybrook andrew"), "phrase") == []
    assert idx.search(words("paradroid"), series=["gazette"]) == []
    plain, parts = snippet("Lots of text before. " * 30 + "PARADROID scores 93% overall. More after. " * 30,
                           ["paradroid"])
    assert plain.startswith("…") and plain.endswith("…") and len(plain) < 300
    hits = [p["text"] for p in parts if p["hit"]]
    assert hits and set(hits) == {"PARADROID"} and plain.index("PARADROID") < 120
    assert "".join(p["text"] for p in parts) == plain


def test_search_terms():
    t = search_terms("What did Zzap give Paradroid?")
    assert t["series"] == ["zzap64"] and t["phrases"] == ["Paradroid"] and t["keywords"] == ["paradroid"]
    t = search_terms('Did Commodore Format like "Mayhem in Monsterland"?')
    assert t["series"] == ["commodore-format"] and t["phrases"][0] == "Mayhem in Monsterland"
    assert search_terms("how fast does it run")["series"] == []      # lowercase "run" is a word, not RUN


def test_index_search_and_resume(app_client):
    calls = []
    with app_client() as c:
        _setup(c, calls, fail=("/download/zzap64-magazine-002/",))
        overview = c.get("/api/magazines").json()
        assert len(overview["series"]) == 7 and overview["indexedTotal"] == 0
        zz = {x["id"]: x for x in overview["series"]}["zzap64"]
        assert zz["issueCount"] == 4 and zz["indexed"] == 0 and not zz["indexing"]
        issues = c.get("/api/magazines/commodore-format/issues").json()["issues"]
        assert issues[0]["readerUrl"] == "https://archive.org/embed/commodore-format/CommodoreFormat-001"
        assert issues[0]["detailsUrl"] == "https://archive.org/details/commodore-format/CommodoreFormat-001"
        assert issues[0]["file"] == "CommodoreFormat-001" and not issues[0]["indexed"]
        assert c.get("/api/magazines/nope/issues").status_code == 404

        assert c.post("/api/magazines/zzap64/index").json()["job"]["running"]
        done = _wait(c, "zzap64")
        assert done["indexed"] == 2                                # 001 and 091; 002 failed, 003 too large
        assert done["job"]["errors"] == 1 and done["job"]["skipped"] == 1 and done["job"]["total"] == 4
        assert "503" in done["job"]["lastError"]
        assert not any("/download/zzap64-magazine-003/" in u for u in calls)    # skipped by size, never fetched

        hits = c.get("/api/magazines/search", params={"q": "paradroid"}).json()["hits"]
        assert [h["issueKey"] for h in hits] == ["zzap64-magazine-001"]
        h = hits[0]
        assert h["issueTitle"] == "ZZap!64 Magazine Issue 001" and h["date"] == "1985-05"
        assert h["link"] == "https://archive.org/details/zzap64-magazine-001?q=paradroid"
        assert any(p["hit"] and p["text"] == "PARADROID" for p in h["parts"]) and "93%" in h["snippet"]
        assert c.get("/api/magazines/search", params={"q": 'para"droid* OR ('}).status_code == 200
        assert c.get("/api/magazines/search", params={"q": "uridium gold", "series": "zzap64"}).json()["mode"] == "any"

        # resumable: the next run only fetches the issue that failed
        calls.clear()
        _setup(c, calls)
        c.post("/api/magazines/zzap64/index")
        done = _wait(c, "zzap64")
        assert done["indexed"] == 3 and done["job"]["total"] == 1
        assert [u for u in calls if "/download/" in u] == [
            "https://archive.org/download/zzap64-magazine-002/zzap64-magazine-002_djvu.txt"]
        assert c.get("/api/magazines/zzap64/issues").json()["issues"][1]["indexed"]
        assert c.delete("/api/magazines/zzap64/index").json()["job"]["running"] is False

        # the multi-file shape indexes from the item's per-issue texts
        c.post("/api/magazines/commodore-format/index")
        assert _wait(c, "commodore-format")["indexed"] == 2
        hit = c.get("/api/magazines/search", params={"q": "Monsterland", "series": "commodore-format"}).json()["hits"][0]
        assert hit["link"] == "https://archive.org/details/commodore-format/CommodoreFormat-001?q=Monsterland"


def test_ask_cites_only_given_excerpts(app_client):
    calls = []
    with app_client() as c:
        _setup(c, calls)
        cont = c.app.state.container
        cont.provider = ExcerptModel(cont.settings, lambda u: {"phrases": ["Paradroid", "Braybrook"]}
                                     if "excerpts" not in u else {
            "answer": f"Zzap!64 gave Paradroid 93% and a Gold Medal [{review_n(u)}]. It was also loved [99].",
            "citations": [review_n(u), 99, "x"]})
        r = c.post("/api/magazines/ask", json={"question": "What did Zzap give Paradroid?"})
        assert r.status_code == 409 and "Make searchable" in r.json()["detail"]
        c.post("/api/magazines/zzap64/index")
        _wait(c, "zzap64")
        r = c.post("/api/magazines/ask", json={"question": "What did Zzap give Paradroid?"})
        assert r.status_code == 200, r.text
        data = r.json()
        n = review_n(cont.provider.calls[-1][1])
        assert data["answer"] == f"Zzap!64 gave Paradroid 93% and a Gold Medal [{n}]. It was also loved."
        assert [x["n"] for x in data["citations"]] == [n]
        cit = data["citations"][0]
        assert cit["issueTitle"] == "ZZap!64 Magazine Issue 001" and cit["date"] == "1985-05"
        assert cit["link"] == "https://archive.org/details/zzap64-magazine-001?q=Paradroid"
        system, user, _ = cont.provider.calls[-1]
        assert "ONLY the numbered magazine excerpts" in system and f"[{n}] Zzap!64 — ZZap!64 Magazine Issue 001 (1985-05)" in user
        assert "Overall 93%" in user


def test_game_reviews(app_client):
    calls = []
    with app_client() as c:
        _setup(c, calls)
        cont = c.app.state.container
        with cont.sf() as s:
            g = Game(group_key="t:paradroid", title="Paradroid", normalized_title="paradroid", format="d64")
            s.add(g)
            s.commit()
            gid = g.id
        assert c.get(f"/api/games/{gid}/magazine-reviews").json()["reviews"] is None
        assert c.post(f"/api/games/{gid}/magazine-reviews").status_code == 409
        assert c.post("/api/games/99999/magazine-reviews").status_code == 404
        c.post("/api/magazines/zzap64/index")
        _wait(c, "zzap64")
        cont.provider = ExcerptModel(cont.settings, lambda u: {"reviews": [
            {"magazine": "Zzap!64", "issue": "1", "date": "May 1985", "score": "93%", "verdict": "A Gold Medal",
             "n": review_n(u)},
            {"magazine": "Made up", "issue": "7", "score": "100%", "verdict": "Invented", "n": 42},
            {"magazine": "Zzap!64", "score": "", "verdict": "", "n": 3 - review_n(u)},
            "junk"]})
        r = c.post(f"/api/games/{gid}/magazine-reviews")
        assert r.status_code == 200, r.text
        reviews = r.json()["reviews"]
        assert len(reviews) == 1
        rv = reviews[0]
        assert rv["score"] == "93%" and rv["verdict"] == "A Gold Medal" and rv["magazine"] == "Zzap!64"
        assert rv["coverUrl"] == f"/api/magazines/cover?key={rv['issueKey']}"          # the reviewing issue's cover
        assert rv["issue"] == "ZZap!64 Magazine Issue 001" and rv["date"] == "1985-05"
        assert rv["link"] == "https://archive.org/details/zzap64-magazine-001?q=Paradroid"
        cached = c.get(f"/api/games/{gid}/magazine-reviews").json()
        assert cached["reviews"] == reviews and cached["madeAt"]


def test_issue_covers_are_fetched_once_and_cached(app_client, monkeypatch):
    import asyncio

    import httpx

    from app.models.magazines import MagazineIssue
    from app.services import magazines as mod

    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(200, content=b"\xff\xd8\xff" + b"0" * 5000)
    real = httpx.AsyncClient
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    with app_client() as c:
        svc = c.app.state.container.magazines
        with svc.sf() as s:
            s.add(MagazineIssue(key="commodore-format/CommodoreFormat-001", series="commodore-format",
                                identifier="commodore-format", stem="CommodoreFormat-001", title="Commodore Format 1", sort=1))
            s.add(MagazineIssue(key="zzap64-magazine-001", series="zzap64", identifier="zzap64-magazine-001",
                                title="Zzap!64 1", sort=1))
            s.commit()
        r = c.get("/api/magazines/cover", params={"key": "commodore-format/CommodoreFormat-001"})
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
        assert seen == ["https://archive.org/download/commodore-format/CommodoreFormat-001/page/n0_w300.jpg"]
        c.get("/api/magazines/cover", params={"key": "commodore-format/CommodoreFormat-001"})
        assert len(seen) == 1                                                      # cached
        assert asyncio.run(svc.prefetch_covers(gap=0)) == 1                         # only the missing one
        assert seen[-1] == "https://archive.org/download/zzap64-magazine-001/page/n0_w300.jpg"
        assert c.get("/api/magazines/cover", params={"key": "nope"}).status_code == 404


def test_ratings_box_and_retrospectives():
    from app.services.magazines import _score_in_text, drop_retrospectives, ratings_box
    review = ("Paradroid is superb. The overall ship design is amazing. Presentation 95% Graphics 92% "
              "Overall 97% THE classic shoot em up. Next: Chimera ... Overall 61%")
    raw, clean = ratings_box(review, ["Paradroid"])
    assert raw.startswith("Overall 97%") and clean == "97%"                   # not the next game's 61%
    garbled = "Paradroid 's one of the best. The overall Ship design … Owrall a7°k3 THE classic"
    raw, clean = ratings_box(garbled, ["Paradroid"])
    assert raw.startswith("Owrall a7°k3") and clean is None                  # the model reads it, marked uncertain
    assert ratings_box("no mention here Overall 50%", ["Paradroid"]) == (None, None)
    assert _score_in_text("97%", garbled, uncertain=True) and not _score_in_text("97%", garbled, uncertain=False)
    assert not _score_in_text("98%", "a development diary with no ratings", uncertain=True)
    kept = drop_retrospectives([{"issue": "7", "date": "1985-11"}, {"issue": "107", "date": "2002-03"},
                                {"issue": "x", "date": None}])
    assert [r["issue"] for r in kept] == ["7", "x"]


def test_quoted_scores_confirm_a_garbled_box():
    from app.services.magazines import _fits, _issue_number, quoted_scores
    later = "Paradroid, Andrew Braybrook's classic, scored a massive 97% back in Issue 7. Uridium got 90% in issue 2."
    assert quoted_scores([later], ["Paradroid"]) == {7: "97%"}
    assert _fits("97%", "Owrall a7°k3 THE") and not _fits("61%", "Owrall a7°k3 THE")
    assert _issue_number("ZZap!64 Magazine Issue 007") == 7
