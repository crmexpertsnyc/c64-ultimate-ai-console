import asyncio
import json
from datetime import date

from app.services.crawlers import find_event_dates, parse_ics, parse_products, split_location
from app.services.geo import normalize_country, place, scope_of

TODAY = date(2026, 10, 3)

ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:abc-1
SUMMARY:Deadline 2026
DTSTART;VALUE=DATE:20261002
DTEND;VALUE=DATE:20261005
LOCATION:ORWOhaus e.V.\\, Frank-Zappa-Straße 19-20\\, 12681 Berlin\\, Germany
URL;VALUE=URI:https://demoparty.berlin/
DESCRIPTION:Your favorite Deadline will\x20
 return!
END:VEVENT
BEGIN:VEVENT
UID:abc-2
SUMMARY:Demosplash 2026
DTSTART;VALUE=DATE:20261106
DTEND;VALUE=DATE:20261109
LOCATION:Carnegie Mellon University\\, Pittsburgh\\, PA\\, USA
URL;VALUE=URI:https://www.demosplash.org/
END:VEVENT
END:VCALENDAR"""


def test_geography_and_scope():
    assert normalize_country("USA") == normalize_country("U.S.") == "United States"
    assert place("Las Vegas, NV", None) == ("Las Vegas", "NV", "United States")
    assert place("Portland, Oregon", "US") == ("Portland", "OR", "United States")
    assert place("Toronto, ON", None) == ("Toronto", "ON", "Canada")
    assert place("Berlin", "Deutschland") == ("Berlin", None, "Germany")
    assert scope_of("Vintage Computer Festival East") == "retro" and scope_of("World of Commodore") == "commodore"


def test_ics_and_locations():
    evs = parse_ics(ICS)
    assert [(e["name"], e["start"], e["end"]) for e in evs] == [
        ("Deadline 2026", date(2026, 10, 2), date(2026, 10, 4)),               # DTEND is exclusive
        ("Demosplash 2026", date(2026, 11, 6), date(2026, 11, 8))]
    assert evs[0]["description"] == "Your favorite Deadline will return!"     # folded line
    assert split_location(evs[0]["location"]) == ("Berlin", "Germany")
    assert split_location(evs[1]["location"]) == ("Pittsburgh, PA", "USA")


def test_dates_on_event_sites():
    f = find_event_dates
    assert f("<p>Join us October 9-11, 2026!</p>", TODAY) == (date(2026, 10, 9), date(2026, 10, 11))
    assert f("July 31 - Aug 2, 2027", TODAY) == (date(2027, 7, 31), date(2027, 8, 2))
    assert f("March 17th-21st, 2027", TODAY) == (date(2027, 3, 17), date(2027, 3, 21))
    assert f("21 to 23 August 2027", TODAY) == (date(2027, 8, 21), date(2027, 8, 23))
    assert f("December 5 & 6, 2026", TODAY) == (date(2026, 12, 5), date(2026, 12, 6))
    assert f("Last year: Sept 12-13, 2026. 2027 dates TBD", TODAY) is None              # over
    # the usual month picks the show over a swap meet in the sidebar
    page = "<aside>Swap Meet Oct. 17, 2026</aside><main>VCF East: April 9-11, 2027</main>"
    assert f(page, TODAY) == (date(2026, 10, 17), date(2026, 10, 17))
    assert f(page, TODAY, months=(4,)) == (date(2027, 4, 9), date(2027, 4, 11))
    # schema.org data and countdown scripts beat other dates written on the page; the text gives the last day
    ld = ('<script type="application/ld+json">{"@type":"Event","startDate":"2027-08-06T10:00","endDate":"2027-08-08"}'
          '</script> Feb 1, 2027')
    assert f(ld, TODAY) == (date(2027, 8, 6), date(2027, 8, 8))
    js = '<script>new Date("May 14, 2027 09:00")</script> April 19, 2027 board meeting. CoCoFEST! May 14-15, 2027'
    assert f(js, TODAY, months=(5,)) == (date(2027, 5, 14), date(2027, 5, 15))


def test_product_feeds():
    rss = """<rss><channel><item><title>EasyFlash 3</title><link>https://store.example/easyflash-3/</link>
      <guid>p1</guid><pubDate>Fri, 02 Oct 2026 10:00:00 GMT</pubDate><isc:price>65.00</isc:price>
      <isc:image>https://store.example/ef3.jpg</isc:image><description>Cartridge</description></item></channel></rss>"""
    p = parse_products({"key": "go4retro", "kind": "bigcommerce-rss", "url": "https://store.example/rss", "filter": False}, rss)
    assert p == [{"guid": "go4retro:p1", "title": "EasyFlash 3", "url": "https://store.example/easyflash-3/",
                  "image": "https://store.example/ef3.jpg", "price": "65.00 USD", "published": "Fri, 02 Oct 2026 10:00:00 GMT",
                  "summary": "Cartridge"}]
    shop = json.dumps({"products": [
        {"id": 1, "title": "ZZAP! 64 Micro Action Issue #33", "handle": "zzap-33", "published_at": "2026-09-30T10:00:00Z",
         "variants": [{"price": "4.99"}], "images": [{"src": "https://cdn.example/z.jpg"}], "tags": []},
        {"id": 2, "title": "CRASH Annual", "handle": "crash", "variants": [{"price": "20"}], "tags": []}]})
    p = parse_products({"key": "fusion", "kind": "shopify", "url": "https://shop.example/products.json", "filter": True}, shop)
    assert [x["title"] for x in p] == ["ZZAP! 64 Micro Action Issue #33"] and p[0]["url"] == "https://shop.example/products/zzap-33"
    woo = json.dumps([{"id": 7, "name": "Replacement C64 PLA", "permalink": "https://vgp.example/pla/",
                       "prices": {"price": "2856", "currency_code": "EUR", "currency_minor_unit": 2}, "categories": []},
                      {"id": 8, "name": "Dreamcast PSU", "permalink": "https://vgp.example/dc/", "prices": {}, "categories": []}])
    p = parse_products({"key": "vgp", "kind": "woo-store", "url": "https://vgp.example/x", "filter": True}, woo)
    assert [(x["title"], x["price"]) for x in p] == [("Replacement C64 PLA", "28.56 EUR")]


def test_calendars_and_sites_become_events(app_client):
    with app_client() as c:
        cont = c.app.state.container
        svc = cont.events
        svc.today = lambda: TODAY
        svc.detail_gap = 0
        pages = {"demoparty.net/demoparties.ical": ICS,
                 "toomanygames.com": "<h1>TooManyGames</h1><p>June 25-27, 2027 · Oaks, PA</p>",
                 "vcfed.org/events/vintage-computer-festival-east": "Swap Meet Oct. 17, 2026 — VCF East April 16-18, 2027"}

        async def fetch(url):
            for k, v in pages.items():
                if k in url:
                    return v
            raise OSError("down")
        svc._fetch = fetch
        r = asyncio.run(svc.crawl_sources())
        assert r["added"] == 2
        # a web-research copy of a show is replaced by the official site's entry
        from app.models.events import Event
        with cont.sf() as s:
            s.add(Event(source="web", ext_id="web:toomanygames:2027-06-25", name="TooManyGames 2027", start=date(2027, 6, 25),
                        end=date(2027, 6, 27), country="USA", url="https://news.example/tmg"))
            s.commit()
        r = asyncio.run(svc.check_sites())
        assert r["found"] == 2 and r["added"] == 2
        data = c.get("/api/events", params={"country": "home"}).json()
        names = {e["name"]: e for e in data["items"]}
        assert set(names) == {"Demosplash 2026", "TooManyGames 2027", "Vintage Computer Festival East 2027"}
        tmg = names["TooManyGames 2027"]
        assert tmg["source"] == "site" and tmg["region"] == "PA" and tmg["home"] and tmg["flag"] == "🇺🇸"
        assert names["Vintage Computer Festival East 2027"]["start"] == "2027-04-16"            # not the swap meet
        assert names["Demosplash 2026"]["region"] == "PA" and names["Demosplash 2026"]["scope"] == "commodore"
        assert [x["code"] for x in data["regions"]] == ["NJ", "PA"]
        # filters: state, dates, scope, home first
        assert [e["name"] for e in c.get("/api/events", params={"country": "home", "region": "NJ"}).json()["items"]] == \
            ["Vintage Computer Festival East 2027"]
        win = c.get("/api/events", params={"from": "2026-10-01", "to": "2026-12-31"}).json()["items"]
        assert {e["name"] for e in win} == {"Deadline 2026", "Demosplash 2026"}
        assert {e["name"] for e in c.get("/api/events", params={"scope": "retro"}).json()["items"]} == \
            {"TooManyGames 2027", "Vintage Computer Festival East 2027"}
        first = c.get("/api/events", params={"home_first": True}).json()["items"]
        assert [e["home"] for e in first] == sorted([e["home"] for e in first], reverse=True)


def test_new_hardware_baseline_then_new(app_client, monkeypatch):
    import httpx

    from app.services import crawlers, shop

    feed = {"key": "fusion", "seller": "Fusion Retro Books", "kind": "shopify",
            "url": "https://shop.example/products.json", "filter": True}
    monkeypatch.setattr(crawlers, "PRODUCT_FEEDS", [feed])
    products = [{"id": 1, "title": "ZZAP! 64 Micro Action Issue #32", "handle": "z32", "variants": [{"price": "4.99"}]}]

    def handler(req):
        return httpx.Response(200, json={"products": products})
    real = httpx.AsyncClient
    monkeypatch.setattr(shop.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    with app_client() as c:
        svc = c.app.state.container.shop
        assert asyncio.run(svc.crawl_products())["added"] == 0          # the first look is the baseline
        products.append({"id": 2, "title": "ZZAP! 64 Micro Action Issue #33", "handle": "z33", "variants": [{"price": "4.99"}]})
        assert asyncio.run(svc.crawl_products())["added"] == 1
        assert asyncio.run(svc.crawl_products())["added"] == 0
        items = c.get("/api/news", params={"kind": "product"}).json()["items"]
        assert items[0]["title"] == "ZZAP! 64 Micro Action Issue #33" and items[0]["releaseType"] == "4.99"
        from datetime import UTC, datetime, timedelta
        since = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        new = c.get("/api/news/new", params={"since": since}).json()
        assert new.get("product") == 1                                  # only the new one counts for the 🆕 badge


def test_web_events_are_tidied(app_client):
    from app.models.events import Event
    with app_client() as c:
        svc = c.app.state.container.events
        with svc.sf() as s:
            for n, (name, start, page) in enumerate([
                    ("Utah Retro GameXpo", date(2027, 8, 13), "https://utahretrogamexpo.com/"),
                    ("Utah Retro GameXpo 2027", date(2027, 8, 13), "https://news.example/utah-2027"),
                    ("VCF Midwest 2027", date(2027, 9, 12), "https://theoasisbbs.com/events/vcfmw-21-2026-brings")]):
                s.add(Event(source="web", ext_id=f"web:{n}", name=name, start=start, end=start, page_url=page))
            s.commit()
        assert svc.tidy_web() == 2
        with svc.sf() as s:
            assert [e.name for e in s.query(Event).all()] == ["Utah Retro GameXpo"]
