# ruff: noqa: E501  (sample feeds are kept as the sites send them)
import asyncio
import json

import pytest
from test_ask import FakeModel

from app.services.news import parse_csdb, parse_csdb_page, parse_itch_page, parse_rss, parse_youtube, raw_score

CSDB = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>CSDb - Latest Releases</title>
<item><title>Ithaka [2sid]</title><link>https://csdb.dk/release/?id=264766&amp;rss</link>
<description><![CDATA[Released by: <a href="https://csdb.dk/group/?id=240">Atlantis</a><br />Type: <a href="x">C64 Music</a><br /><a href="https://csdb.dk/release/download.php?id=325894" title="https://csdb.dk/getinternalfile.php/283701/ithaka_8580_2sid.prg">Download</a>]]></description>
<guid>https://csdb.dk/release/?id=264766</guid><pubDate>Sat, 03 Oct 2026 00:00:00 +0200</pubDate></item>
<item><title>Leet or Just a Lamer? (16MB REU x3)</title><link>https://csdb.dk/release/?id=264764&amp;rss</link>
<description><![CDATA[Released by: <a href="x">Obliterator918</a><br />Type: <a href="x">REU Release</a><br /><a href="https://csdb.dk/release/download.php?id=325893" title="https://www.obliterator918.com/downloads/LAMENOTER.zip">Download</a><br /><img alt="x" src="https://csdb.dk/gfx/releases/264000/264764.gif" />]]></description>
<guid>https://csdb.dk/release/?id=264764</guid><pubDate>Sat, 03 Oct 2026 00:00:00 +0200</pubDate></item>
<item><title>Fruit RSI +2D</title><link>https://csdb.dk/release/?id=264763&amp;rss</link>
<description><![CDATA[Released by: <a href="x">Shadow</a><br />Type: <a href="x">C64 Crack</a><br /><a href="https://csdb.dk/release/download.php?id=325891" title="https://csdb.dk/getinternalfile.php/283699/Fruit RSI %2B2D %5BShadow%5D.d64">Download</a><br /><img alt="x" src="https://csdb.dk/gfx/releases/264000/264763.gif" />]]></description>
<guid>https://csdb.dk/release/?id=264763</guid><pubDate>Fri, 02 Oct 2026 00:00:00 +0200</pubDate></item>
<item><title>Plus4 thing</title><link>https://csdb.dk/release/?id=1&amp;rss</link>
<description><![CDATA[Type: <a href="x">Plus/4 Demo</a>]]></description><guid>https://csdb.dk/release/?id=1</guid></item>
<item><title>Party Demo</title><link>https://csdb.dk/release/?id=264760&amp;rss</link>
<description><![CDATA[Released by: <a href="x">Booze Design</a><br />Type: <a href="x">C64 Demo</a><br /><a href="https://csdb.dk/release/download.php?id=1" title="https://csdb.dk/getinternalfile.php/1/party.d64">Download</a>]]></description>
<guid>https://csdb.dk/release/?id=264760</guid><pubDate>Thu, 01 Oct 2026 00:00:00 +0200</pubDate></item>
</channel></rss>"""

ITCH = """<?xml version="1.0" encoding="UTF-8" ?><rss version="2.0"><channel>
<item><guid>https://mlouvet.itch.io/sram-64</guid><title>Sram 64 [Free] [Adventure]</title><plainTitle>Sram 64</plainTitle>
<imageurl>https://img.itch.zone/a.jpg</imageurl><price>$0.00</price><link>https://mlouvet.itch.io/sram-64</link>
<description><![CDATA[<p>A text adventure <b>for the C64</b>.</p><script>alert(1)</script>]]></description>
<pubDate>Fri, 02 Oct 2026 10:00:00 GMT</pubDate></item>
<item><guid>bad</guid><title>No link</title><link>javascript:alert(1)</link></item>
</channel></rss>"""

YT = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">
<entry><yt:videoId>abcDEF12345</yt:videoId><title>Fixing a C64 breadbin</title><published>2026-10-01T12:00:00+00:00</published>
<media:group><media:description>Repairing the SID.</media:description></media:group></entry>
<entry><yt:videoId>zzzDEF12345</yt:videoId><title>Zune review</title><published>2026-10-01T12:00:00+00:00</published>
<media:group><media:description>Microsoft music player.</media:description></media:group></entry>
<entry><yt:videoId>sssDEF12345</yt:videoId><title>Commodore fun #shorts</title><published>2026-10-01T12:00:00+00:00</published></entry>
</feed>"""


def test_parsing_the_feeds():
    rel = parse_csdb(CSDB)
    assert [r["title"] for r in rel] == ["Ithaka [2sid]", "Leet or Just a Lamer? (16MB REU x3)", "Fruit RSI +2D", "Party Demo"]
    music, reu, crack, demo = rel
    assert music["category"] == "music" and music["playable"] and music["author"] == "Atlantis"   # a .prg tune
    assert reu["category"] == "game" and not reu["playable"]          # its file is not hosted on CSDb
    assert crack["category"] == "game" and crack["playable"] and crack["csdb_id"] == "264763"
    assert crack["url"] == "https://csdb.dk/release/?id=264763" and crack["image"].endswith("264763.gif")
    assert demo["category"] == "demo" and demo["playable"]
    itch = parse_rss(ITCH, "release")
    assert len(itch) == 1 and itch[0]["title"] == "Sram 64" and itch[0]["release_type"] == "Free"
    assert itch[0]["summary"] == "A text adventure for the C64 ." and "alert" not in itch[0]["summary"]
    vids = parse_youtube(YT, "Jan Beta", c64only=False)
    assert [v["video_id"] for v in vids] == ["abcDEF12345"]            # not about the C64 / shorts are skipped
    assert len(parse_youtube(YT, "Official Commodore", c64only=True)) == 2
    with pytest.raises(ValueError):
        parse_rss('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss/>', "news")


def _fake_fetch(fail=()):
    async def fetch(url):
        if any(f in url for f in fail):
            raise OSError("offline")
        if "csdb" in url:
            return CSDB
        if "itch.io" in url:
            return ITCH
        if "youtube" in url:
            return YT
        return "<rss><channel></channel></rss>"
    return fetch


def test_monitor_stores_new_items_once(app_client):
    with app_client() as c:
        svc = c.app.state.container.news
        svc._fetch = _fake_fetch(fail=("indieretronews",))
        first = c.post("/api/news/refresh").json()
        assert first["added"] > 5 and "Indie Retro News" in first["errors"]
        assert c.post("/api/news/refresh").json()["added"] == 0           # nothing new the second time
        rel = c.get("/api/news", params={"kind": "release"}).json()
        assert rel["items"][0]["title"] == "Ithaka [2sid]" and rel["counts"]["video"] >= 1
        games = c.get("/api/news", params={"kind": "release", "category": "game"}).json()["items"]
        assert {g["title"] for g in games} == {"Leet or Just a Lamer? (16MB REU x3)", "Fruit RSI +2D", "Sram 64"}
        vid = c.get("/api/news", params={"kind": "video"}).json()["items"][0]
        assert vid["videoId"] and vid["sourceLabel"]
        assert c.get("/api/news", params={"q": "booze"}).json()["items"][0]["title"] == "Party Demo"
        assert c.get("/api/news/new", params={"since": "2000-01-01T00:00:00"}).json()["total"] == rel["counts"]["release"] + rel["counts"]["video"]
        assert c.get("/api/news/new", params={"since": "2999-01-01T00:00:00Z"}).json()["total"] == 0


def test_adding_a_new_release_to_the_library(app_client, monkeypatch):
    with app_client() as c:
        cont = c.app.state.container
        cont.news._fetch = _fake_fetch()
        c.post("/api/news/refresh")
        items = {i["title"]: i for i in c.get("/api/news", params={"kind": "release"}).json()["items"]}
        calls = []

        async def import_url(url, title=None):
            calls.append(url)
            from app.library.titles import title_key
            from app.models.db import Game
            with cont.sf() as s:
                g = Game(group_key=f"t:{title}", title=title, normalized_title=title_key(title), format="d64")
                s.add(g)
                s.commit()
                return g.id

        monkeypatch.setattr(cont.imports, "import_url", import_url)
        crack = items["Fruit RSI +2D"]
        gid = c.post(f"/api/news/{crack['id']}/add").json()["gameId"]
        assert calls == ["https://csdb.dk/release/?id=264763"]
        assert c.post(f"/api/news/{crack['id']}/add").json()["gameId"] == gid and len(calls) == 1   # once
        assert c.get("/api/news", params={"q": "Fruit"}).json()["items"][0]["gameId"] == gid
        r = c.post(f"/api/news/{items['Leet or Just a Lamer? (16MB REU x3)']['id']}/add")
        assert r.status_code == 409


def test_weekly_digest(app_client):
    with app_client() as c:
        cont = c.app.state.container
        assert c.post("/api/news/digest").status_code == 409                  # nothing yet
        cont.news._fetch = _fake_fetch()
        c.post("/api/news/refresh")
        items = c.get("/api/news").json()["items"]
        ids = [i["id"] for i in items]
        cont.provider = FakeModel(cont.settings, json.dumps({
            "headline": "Booze Design is back", "intro": "A big week.",
            "picks": [{"id": ids[0], "why": "Fresh SID tune"}, {"id": 999999, "why": "made up"}, {"id": ids[0]}]}))
        d = c.post("/api/news/digest").json()["digest"]
        assert d["headline"] == "Booze Design is back" and [p["item"]["id"] for p in d["picks"]] == [ids[0]]
        assert c.get("/api/news/digest").json()["digest"]["intro"] == "A big week."



CSDB_RATED = """<table><tr><td><b>User rating:</b></td><td><img alt="*">&nbsp; 9.7/10 (129 votes) &nbsp; <a>See votestatistics</a></td></tr>
<tr><td></td><td>&nbsp; 10/10 (45 votes) - Public votes only.</td></tr></table>
<a href="x">file.d64</a> (downloads: 1200)<br><a href="y">mirror.d64</a> (downloads: 34)<br>
· <a href="#comments">User Comments</a> (12)<br>"""
CSDB_NEW = """<b>User rating:</b>awaiting 8 votes (5 left) &nbsp; <a>See votestatistics</a>
(downloads: 103) · User Comments (1)"""
ITCH_PAGE = """<script type="application/ld+json">{"@type":"Product","aggregateRating":{"ratingCount":67,"@type":"AggregateRating","ratingValue":"4.9"}}</script>"""


def test_reading_popularity_from_the_sources():
    assert parse_csdb_page(CSDB_RATED) == {"downloads": 1234, "comments": 12, "votes": 129, "rating": 9.7}
    assert parse_csdb_page(CSDB_NEW) == {"downloads": 103, "comments": 1, "votes": 3}        # 3 votes, no average yet
    assert parse_csdb_page("<html>nothing</html>") == {"downloads": 0, "comments": 0, "votes": 0}
    assert parse_itch_page(ITCH_PAGE) == {"ratings": 67, "stars": 4.9}
    assert parse_itch_page("<html></html>") == {"ratings": 0}
    yt = parse_youtube(YT.replace("<media:description>Repairing the SID.</media:description>",
                                  '<media:description>Repairing the SID.</media:description><media:community>'
                                  '<media:starRating count="433" average="5.00" min="1" max="5"/>'
                                  '<media:statistics views="10218"/></media:community>'), "Jan Beta", c64only=False)
    assert yt[0]["stats"] == {"views": 10218, "likes": 433}
    # a few votes of 10 don't beat many votes of 9.5; more downloads and talk count
    many = raw_score("csdb", {"downloads": 900, "comments": 20, "votes": 60, "rating": 9.5})
    few = raw_score("csdb", {"downloads": 900, "comments": 20, "votes": 9, "rating": 10.0})
    quiet = raw_score("csdb", {"downloads": 40, "comments": 0, "votes": 0})
    assert many > few > quiet
    assert raw_score("indieretronews", {"x": 1}) is None and raw_score("csdb", None) is None


def test_sorting_releases_by_popularity_and_trend(app_client):
    with app_client() as c:
        svc = c.app.state.container.news
        pages = {"264763": CSDB_RATED, "264760": CSDB_NEW, "264766": "(downloads: 400) User Comments (3)",
                 "264764": "(downloads: 5)"}

        async def fetch(url):
            for rid, page in pages.items():
                if url.endswith(f"id={rid}"):
                    return page
            if "sram-64" in url:
                return ITCH_PAGE
            return await _fake_fetch()(url)
        svc._fetch = fetch
        c.post("/api/news/refresh")
        assert asyncio.run(svc.update_stats(gap=0)) == 5                    # 4 CSDb + 1 itch.io pages
        assert asyncio.run(svc.update_stats(gap=0)) == 0                    # fresh: not read again yet
        top = c.get("/api/news", params={"kind": "release", "sort": "popular"}).json()["items"]
        csdb = [i["title"] for i in top if i["source"] == "csdb"]
        assert csdb == ["Fruit RSI +2D", "Ithaka [2sid]", "Party Demo", "Leet or Just a Lamer? (16MB REU x3)"]
        assert top[0]["popularity"] >= 80 and top[0]["stats"]["votes"] == 129
        sram = next(i for i in top if i["title"] == "Sram 64")
        assert sram["popularity"] == 50.0 and sram["stats"] == {"ratings": 67, "stars": 4.9}   # alone in its source
        trending = c.get("/api/news", params={"kind": "release", "sort": "trending"}).json()["items"]
        assert all(i["trend"] is not None for i in trending[:5])
        newest = c.get("/api/news", params={"kind": "release"}).json()["items"]
        assert newest[0]["title"] == "Ithaka [2sid]"
        assert c.get("/api/news", params={"sort": "loudest"}).status_code == 422
