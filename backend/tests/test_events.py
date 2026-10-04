# ruff: noqa: E501  (sample feeds are kept as the sites send them)
import asyncio
import json
from datetime import date

import pytest
from test_ask import FakeModel

from app.services import ask as ask_mod
from app.services.retro_events import (
    RESEARCH_QUERIES,
    filter_research,
    ics_calendar,
    ics_escape,
    ics_fold,
    parse_csdb_event_page,
    parse_csdb_events,
    parse_held_on,
    split_country,
)

TODAY = date(2026, 10, 3)

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>CSDb - Upcoming Events</title>
<item><title>Yogi's &amp;amp; Flex' Melodic 2SID Compo 2026</title><link>https://csdb.dk/event/?id=3679&amp;rss</link>
<description><![CDATA[<a href="https://csdb.dk/event/?id=3679&rss"><img src="https://csdb.dk/gfx/events/3000/3679.jpg" alt="" border="0"></a><br /><i>- There can be only 2! :)</i><br /><br />Event type: Standalone Compo<br /><br />Held on: 1 October - 31 December 2026<br />]]></description>
<guid>https://csdb.dk/event/?id=3679</guid></item>
<item><title>Deadline 2026 (Germany)</title><link>https://csdb.dk/event/?id=3650&amp;rss</link>
<description><![CDATA[<a href="x"><img src="https://csdb.dk/gfx/events/3000/3650.jpg"></a><br /><i>- ApoCalypso</i><br /><br />Event type: Demo Party<br /><br />Held on: 2 - 4 October 2026<br />]]></description>
<guid>https://csdb.dk/event/?id=3650</guid></item>
<item><title>Transmission64 2026</title><link>https://csdb.dk/event/?id=3609&amp;rss</link>
<description><![CDATA[<i>- The C64 Online Demoparty</i><br /><br />Event type: Demo Party<br /><br />Held on: 28 November 2026<br />]]></description>
<guid>https://csdb.dk/event/?id=3609</guid></item>
<item><title>Fioniadata 2027 (Denmark)</title><link>https://csdb.dk/event/?id=3657&amp;rss</link>
<description><![CDATA[<br />Event type: Demo Party, Meeting<br /><br />Held on: 12 - 14 March 2027<br />]]></description>
<guid>https://csdb.dk/event/?id=3657</guid></item>
<item><title>Bad dates (Nowhere)</title><link>https://csdb.dk/event/?id=1&amp;rss</link>
<description><![CDATA[Held on: sometime soon]]></description><guid>https://csdb.dk/event/?id=1</guid></item>
</channel></rss>"""

PAGE_DEADLINE = """<font size=6>Deadline 2026</font>
<b>Dates :</b><br>
2 - 4 October 2026<br><br>
<b>Place :</b><br>
ORWOhaus e.V, Frank-Zappa-Stra&szlig;e 19<br>12681 Berlin<br>Germany<br><br>
<b>Website :</b><br>
<a href="https://www.demoparty.berlin/" target=_blank>https://www.demoparty.berlin/</a><br><br>
<b>Organizers :</b><br>"""
PAGE_STREAM = """<b>Place :</b><br>
 Langeskov<br>Denmark<br><br>
<b>Website :</b><br>
<a href="https://www.twitch.tv/somerparty" target=_blank>https://www.twitch.tv/somerparty</a><br><br>"""


@pytest.mark.parametrize(("text", "start", "end"), [
    ("28 November 2026", date(2026, 11, 28), date(2026, 11, 28)),
    ("12 - 14 March 2027", date(2027, 3, 12), date(2027, 3, 14)),
    ("19 - 21 March 2027", date(2027, 3, 19), date(2027, 3, 21)),
    ("30 April - 2 May 2027", date(2027, 4, 30), date(2027, 5, 2)),
    ("30 December 2026 - 2 January 2027", date(2026, 12, 30), date(2027, 1, 2)),
    ("30 December - 2 January 2027", date(2026, 12, 30), date(2027, 1, 2)),
    ("1 October - 31 December 2026", date(2026, 10, 1), date(2026, 12, 31)),
    ("12 – 14 March 2027", date(2027, 3, 12), date(2027, 3, 14)),          # en dash
    ("12th - 14th Mar 2027", date(2027, 3, 12), date(2027, 3, 14)),
    ("March 2027", date(2027, 3, 1), date(2027, 3, 31)),                   # month only: the whole month
    ("  28  November   2026 ", date(2026, 11, 28), date(2026, 11, 28)),
])
def test_held_on_dates(text, start, end):
    assert parse_held_on(text) == (start, end)


@pytest.mark.parametrize("text", ["", "sometime soon", "31 February 2027", "14 - 12 March 2027", "12 - 14 Smarch 2027",
                                  "12 March", "1 - 2 - 3 May 2027"])
def test_held_on_rejects_nonsense(text):
    assert parse_held_on(text) is None


def test_csdb_rss_parsing():
    ev = parse_csdb_events(RSS)
    assert [e["ext_id"] for e in ev] == ["csdb:3679", "csdb:3650", "csdb:3609", "csdb:3657"]   # bad dates skipped
    compo, deadline, t64, fionia = ev
    assert compo["name"] == "Yogi's & Flex' Melodic 2SID Compo 2026" and compo["country"] is None
    assert compo["tagline"] == "There can be only 2! :)" and compo["image"].endswith("3679.jpg")
    assert deadline["name"] == "Deadline 2026" and deadline["country"] == "Germany" and deadline["type"] == "Demo Party"
    assert (deadline["start"], deadline["end"]) == (date(2026, 10, 2), date(2026, 10, 4))
    assert deadline["page_url"] == "https://csdb.dk/event/?id=3650"
    assert t64["tagline"] == "The C64 Online Demoparty" and t64["image"] is None
    assert fionia["type"] == "Demo Party, Meeting" and fionia["tagline"] is None
    assert split_country("FOReVER 2027 - 8-bit City (Slovakia)") == ("FOReVER 2027 - 8-bit City", "Slovakia")
    with pytest.raises(ValueError):
        parse_csdb_events('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss/>')


def test_csdb_event_page():
    assert parse_csdb_event_page(PAGE_DEADLINE) == {"website": "https://www.demoparty.berlin/", "city": "Berlin",
                                                    "country": "Germany"}
    p = parse_csdb_event_page(PAGE_STREAM)
    assert p["stream"] == "https://www.twitch.tv/somerparty" and p["city"] == "Langeskov"
    assert parse_csdb_event_page("<html>nothing</html>") == {}
    assert "website" not in parse_csdb_event_page('<b>Website :</b><br><a href="javascript:alert(1)">x</a>')


RESULTS = [{"title": "Retro Computer Festival 2027", "url": "https://www.rcf.example/2027/", "description": "16-17 May 2027"},
           {"title": "VCF East", "url": "https://vcfed.example/vcf-east", "description": "April 2027"},
           {"title": "No link", "url": "ftp://x.example/", "description": ""}]


def test_research_keeps_only_sourced_links():
    data = {"events": [
        {"name": "Retro Computer Festival", "start": "2027-05-16", "end": "2027-05-17", "city": "Cambridge",
         "country": "UK", "type": "Computer Fair", "url": "https://rcf.example/2027", "sources": [1]},     # = result 1
        {"name": "VCF East", "start": "2027-04-10", "end": "2027-04-12", "type": "festival",
         "url": "https://vcfed.example/schedule", "sources": [2]},                                       # same site as cited 2
        {"name": "Made-up Expo", "start": "2027-01-01", "url": "https://invented.example/", "sources": [1]},  # not a result
        {"name": "Uncited", "start": "2027-02-01", "url": "https://vcfed.example/other", "sources": []},     # no citation
        {"name": "No url but cited", "start": "2027-03-01", "sources": [2, 9, "x"]},
        {"name": "No url, no source", "start": "2027-03-01"},
        {"name": "Over already", "start": "2026-09-01", "end": "2026-09-02", "url": RESULTS[0]["url"], "sources": [1]},
        {"name": "Too far", "start": "2029-01-01", "url": RESULTS[0]["url"], "sources": [1]},
        {"name": "Bad date", "start": "next spring", "url": RESULTS[0]["url"]},
        {"name": "Backwards", "start": "2027-05-02", "end": "2027-05-01", "url": RESULTS[0]["url"]},
        {"name": "Bad scheme", "start": "2027-05-02", "url": "javascript:alert(1)", "sources": [1]},
        "not an object",
    ]}
    kept, dropped = filter_research(data, RESULTS, TODAY)
    assert [e["name"] for e in kept] == ["Retro Computer Festival", "VCF East", "No url but cited"]
    assert dropped == 9
    rcf, vcf, cited = kept
    assert rcf["url"] == "https://www.rcf.example/2027/" and rcf["type"] == "Computer Fair" and rcf["source"] == "web"
    assert vcf["url"] == "https://vcfed.example/schedule" and vcf["type"] == "Vintage Computer Festival"
    assert cited["url"] == "https://vcfed.example/vcf-east" and cited["end"] == date(2027, 3, 1)
    result_urls = {r["url"] for r in RESULTS}
    assert all(e["url"] in result_urls or e["url"].startswith("https://vcfed.example/") for e in kept)
    assert filter_research({"events": "nope"}, RESULTS, TODAY) == ([], 0)


def test_ics_output():
    from app.models.events import Event
    e = Event(id=7, source="user", ext_id="user:abc", name="Party, with; commas\\ and\nnewline",
              start=date(2026, 11, 28), end=date(2026, 11, 29), city="Berlin", country="Germany",
              url="https://example.test/p?a=1,2", type="Demo Party, Meeting", notes="Bring a 1541 " * 10)
    ics = ics_calendar([e], "Test")
    assert ics.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n") and ics.endswith("END:VCALENDAR\r\n")
    assert "\n" not in ics.replace("\r\n", "")                                          # CRLF only
    unfolded = ics.replace("\r\n ", "")
    assert "DTSTART;VALUE=DATE:20261128\r\n" in unfolded
    assert "DTEND;VALUE=DATE:20261130\r\n" in unfolded                                  # exclusive end
    assert "SUMMARY:Party\\, with\\; commas\\\\ and\\nnewline\r\n" in unfolded
    assert "LOCATION:Berlin\\, Germany" in unfolded and "URL:https://example.test/p?a=1,2" in unfolded
    assert "CATEGORIES:Demo Party,Meeting" in unfolded and "UID:event-7-user-abc@" in unfolded
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))
    assert ics_escape("a,b;c\\d\r\ne") == "a\\,b\\;c\\\\d\\ne"
    folded = ics_fold("X:" + "ü" * 60)                                                  # never splits a UTF-8 char
    assert all(len(p.encode()) <= 75 for p in folded.split("\r\n")) and folded.replace("\r\n ", "") == "X:" + "ü" * 60


def _fetch(pages=None):
    pages = pages or {}

    async def fetch(url):
        if "upcomingevents" in url:
            return RSS
        for k, v in pages.items():
            if url.endswith(k):
                return v
        raise OSError("offline")
    return fetch


def _svc(c):
    svc = c.app.state.container.events
    svc.today = lambda: TODAY
    svc.detail_gap = 0
    return svc


def test_csdb_refresh_live_now_and_details(app_client):
    with app_client() as c:
        cont = c.app.state.container
        svc = _svc(c)
        assert svc._task is None and cont.firmware._task is None              # no background loops in tests
        svc._fetch = _fetch({"id=3650": PAGE_DEADLINE})
        r = c.post("/api/events/refresh").json()
        assert r["added"] == 4 and r["errors"] == {}
        assert c.post("/api/events/refresh").json()["added"] == 0
        asyncio.run(svc._details())          # (the refresh reads them in the background too) — only Deadline's page is reachable
        assert asyncio.run(svc._details()) == 0                                # read: not again for a week
        from app.models.db import NewsItem
        with cont.sf() as s:
            s.add(NewsItem(guid="yt:t64", source="youtube:transmission64", kind="video", title="T64 live",
                           url="https://www.youtube.com/watch?v=t64abcdefgh", video_id="t64abcdefgh"))
            s.commit()
        data = c.get("/api/events").json()
        assert [e["name"] for e in data["items"]][:2] == ["Yogi's & Flex' Melodic 2SID Compo 2026", "Deadline 2026"]
        assert [e["name"] for e in data["live"]] == ["Deadline 2026"]               # the 3-month compo is only "ongoing"
        dl = data["live"][0]
        assert dl["url"] == "https://www.demoparty.berlin/" and dl["city"] == "Berlin" and dl["sourceLabel"] == "CSDb"
        assert data["liveVideos"][0]["videoId"] == "t64abcdefgh"
        assert data["countries"] == ["Denmark", "Germany"] and "Meeting" in data["types"]
        assert [e["name"] for e in c.get("/api/events", params={"country": "Denmark"}).json()["items"]] == ["Fioniadata 2027"]
        assert [e["name"] for e in c.get("/api/events", params={"type": "meeting"}).json()["items"]] == ["Fioniadata 2027"]
        assert [e["name"] for e in c.get("/api/events", params={"q": "online"}).json()["items"]] == ["Transmission64 2026"]
        ics = c.get(f"/api/events/{dl['id']}.ics")
        assert ics.status_code == 200 and ics.headers["content-type"].startswith("text/calendar")
        assert "attachment" in ics.headers["content-disposition"] and "SUMMARY:Deadline 2026" in ics.text
        cal = c.get("/api/events/calendar.ics")
        assert cal.text.count("BEGIN:VEVENT") == 4
        assert c.get("/api/events/99999.ics").status_code == 404


def test_manual_events_rules(app_client):
    with app_client() as c:
        svc = _svc(c)
        svc._fetch = _fetch()
        c.post("/api/events/refresh")
        mine = c.post("/api/events", json={"name": "C64 club night", "start": "2026-12-05", "city": "Oslo",
                                           "country": "Norway", "url": "https://club.example/", "type": "Meeting",
                                           "notes": "Bring disks"}).json()
        assert mine["source"] == "user" and mine["sourceLabel"] == "yours" and mine["end"] == "2026-12-05" and mine["editable"]
        assert c.post("/api/events", json={"name": "x", "start": "2026-12-05", "end": "2026-12-01"}).status_code == 409
        assert c.post("/api/events", json={"name": "x", "start": "2026-12-05", "url": "javascript:alert(1)"}).status_code == 409
        assert c.post("/api/events", json={"name": "", "start": "2026-12-05"}).status_code == 422
        edited = c.patch(f"/api/events/{mine['id']}", json={"name": "C64 club xmas", "end": "2026-12-06"}).json()
        assert edited["name"] == "C64 club xmas" and edited["end"] == "2026-12-06" and edited["city"] == "Oslo"
        assert c.patch(f"/api/events/{mine['id']}", json={"end": "2026-12-01"}).status_code == 409
        csdb = next(e for e in c.get("/api/events").json()["items"] if e["source"] == "csdb")
        assert c.patch(f"/api/events/{csdb['id']}", json={"name": "Renamed"}).status_code == 409   # not yours
        assert c.delete(f"/api/events/{csdb['id']}").status_code == 409
        assert c.patch(f"/api/events/{csdb['id']}", json={"hidden": True}).json()["hidden"] is True
        listed = c.get("/api/events").json()
        assert csdb["id"] not in [e["id"] for e in listed["items"]] and listed["hiddenCount"] == 1
        assert csdb["id"] in [e["id"] for e in c.get("/api/events", params={"include_hidden": True}).json()["items"]]
        c.patch(f"/api/events/{csdb['id']}", json={"hidden": False})
        assert c.delete(f"/api/events/{mine['id']}").json() == {"deleted": mine["id"]}
        assert c.delete(f"/api/events/{mine['id']}").status_code == 404
        assert c.patch("/api/events/99999", json={"hidden": True}).status_code == 404


def test_research_needs_ai_and_web_search(app_client, monkeypatch):
    with app_client() as c:
        _svc(c)
        r = c.post("/api/events/research")
        assert r.status_code == 409 and "AI model" in r.json()["detail"]
        cont = c.app.state.container
        cont.provider = FakeModel(cont.settings, "{}")
        r = c.post("/api/events/research")
        assert r.status_code == 409 and "web search" in r.json()["detail"]


def test_research_stores_sourced_events(app_client, monkeypatch):
    queries = []

    async def fake_search(query, key, count=8, timeout=10):  # noqa: ANN001
        queries.append(query)
        return RESULTS

    monkeypatch.setattr(ask_mod, "brave_search", fake_search)
    from app.services import ai_json
    monkeypatch.setattr(ai_json, "brave_search", fake_search)
    with app_client() as c:
        cont = c.app.state.container
        svc = _svc(c)
        svc._fetch = _fetch()
        c.post("/api/events/refresh")
        cont.config.update({"BRAVE_API_KEY": "test-key"})
        cont.provider = FakeModel(cont.settings, json.dumps({"events": [
            {"name": "Retro Computer Festival", "start": "2027-05-16", "end": "2027-05-17", "city": "Cambridge",
             "country": "UK", "type": "Computer Fair", "url": "https://rcf.example/2027", "sources": [1]},
            {"name": "Fioniadata 2027", "start": "2027-03-12", "end": "2027-03-14", "url": RESULTS[1]["url"], "sources": [2]},
            {"name": "Invented Expo", "start": "2027-01-01", "url": "https://invented.example/"}]}))
        r = c.post("/api/events/research").json()
        n = len(RESEARCH_QUERIES)
        assert r["added"] == 1 and r["dropped"] == n and len(queries) == n                # 1 invented URL × every query
        web = [e for e in c.get("/api/events").json()["items"] if e["source"] == "web"]
        assert [(e["name"], e["url"], e["sourceLabel"]) for e in web] == [
            ("Retro Computer Festival", "https://www.rcf.example/2027/", "web")]           # Fioniadata is already on CSDb
        assert c.post("/api/events/research").json()["added"] == 0
        system, user, _ = cont.provider.calls[0]
        assert "[1] Retro Computer Festival 2027" in user and "2026-10-03" in user
