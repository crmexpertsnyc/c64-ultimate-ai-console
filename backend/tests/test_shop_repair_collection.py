import asyncio
import json

import httpx
from test_ask import FakeModel

from app.library.titles import title_key
from app.services.shop import EbayClient, ebay_search_url, validate_catalog


def _game(c, title, fmt="d64"):
    from app.models.db import Game, Media
    with c.app.state.container.sf() as s:
        g = Game(group_key=f"t:{title}", title=title, normalized_title=title_key(title), format=fmt)
        s.add(g)
        s.flush()
        s.add(Media(game_id=g.id, path=f"/tmp/{title}.{fmt}", format=fmt))
        s.commit()
        return g.id


def test_catalog_is_validated_and_builtin_has_real_items(app_client):
    bad = validate_catalog({"sellers": [{"id": "x", "name": "X", "url": "javascript:alert(1)"}],
                            "items": [{"id": "a", "name": "A", "links": [{"seller": "x", "url": "http://plain.example"}]},
                                      {"id": "b", "name": "B", "category": "weird", "tags": ["psu"],
                                       "links": [{"seller": "x", "url": "https://ok.example/b", "price": 9,
                                                  "affiliate": True}]}]})
    assert [i["id"] for i in bad["items"]] == ["b"]                        # no https link → dropped
    assert bad["items"][0]["category"] == "accessories" and bad["items"][0]["links"][0]["affiliate"]
    assert bad["sellers"][0]["url"] is None
    with app_client() as c:
        cat = c.get("/api/shop/catalog").json()
        assert cat["source"] == "built-in" and len(cat["items"]) >= 30 and cat["disclosure"]
        assert all(ln["url"].startswith("https://") for i in cat["items"] for ln in i["links"])
        assert not any(ln["affiliate"] for i in cat["items"] for ln in i["links"])   # none of them has a program
        psu = c.get("/api/shop/catalog", params={"tag": "overvoltage"}).json()["items"]
        assert psu and all("overvoltage" in i["tags"] for i in psu)


def test_suggestions_know_the_setup(app_client):
    with app_client() as c:
        gid = _game(c, "Sonic the Hedgehog", fmt="crt")
        reu = _game(c, "Sam's Journey REU edition")
        g = c.get("/api/shop/suggest", params={"context": "game", "game_id": gid}).json()["suggestions"]
        assert g and "cartridge" in g[0]["why"] and any("easyflash" in i["tags"] for i in g[0]["items"])
        r = c.get("/api/shop/suggest", params={"context": "game", "game_id": reu}).json()["suggestions"]
        assert any("REU" in s["why"] for s in r)
        k = c.get("/api/shop/suggest", params={"context": "controller", "using": "keyboard"}).json()["suggestions"]
        assert k and any("wireless-joystick" in i["tags"] for i in k[0]["items"])


def test_starter_kit_only_from_the_catalog(app_client):
    with app_client() as c:
        cont = c.app.state.container
        assert c.post("/api/shop/advisor", json={}).status_code == 409       # no AI configured
        cont.provider = FakeModel(cont.settings, json.dumps({"intro": "Here's a kit.", "kit": [
            {"id": "tac-2", "why": "A great joystick", "priority": "must"},
            {"id": "[commodore-av-cable]", "why": "Sharp picture"},
            {"id": "made-up-thing", "why": "x"}], "tips": ["Use a safe PSU"]}))
        kit = c.post("/api/shop/advisor", json={"prompt": "I just got a C64 Ultimate"}).json()
        assert [k["item"]["id"] for k in kit["kit"]] == ["tac-2", "commodore-av-cable"]
        assert kit["kit"][0]["priority"] == "must" and kit["tips"] == ["Use a safe PSU"]


class _S:
    EBAY_CLIENT_ID = "id"
    EBAY_CLIENT_SECRET = "secret"
    EBAY_CAMPAIGN_ID = "5338000000"
    EBAY_MARKETPLACE = "EBAY_US"


def _ebay_transport(items, seen):
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 7200})
        return httpx.Response(200, json={"itemSummaries": items})
    return httpx.MockTransport(handler)


def test_ebay_listings_are_affiliate_tagged():
    url = ebay_search_url("c64 breadbin", _S)
    assert "campid=5338000000" in url and "_nkw=c64+breadbin" in url
    assert "LH_Sold=1" in ebay_search_url("x", _S, sold=True)
    seen = []
    items = [{"itemId": "v1|1|0", "title": "Commodore 64 breadbin boxed", "price": {"value": "120.00", "currency": "USD"},
              "itemWebUrl": "https://www.ebay.com/itm/1", "itemAffiliateWebUrl": "https://www.ebay.com/itm/1?campid=5338000000"},
             {"itemId": "v1|2|0", "title": "no price", "itemWebUrl": "https://www.ebay.com/itm/2", "price": {}}]
    client = EbayClient(lambda: _S, http=httpx.AsyncClient(transport=_ebay_transport(items, seen)))
    out = asyncio.run(client.search("c64 breadbin", max_price=150))
    assert [i["price"] for i in out] == [120.0] and out[0]["affiliate"] and "campid" in out[0]["url"]
    search = seen[-1]
    assert search.headers["X-EBAY-C-ENDUSERCTX"] == "affiliateCampaignId=5338000000"
    assert "price%3A%5B..150.00%5D" in str(search.url) or "price:[..150.00]" in str(search.url)


def test_price_watch_makes_a_deal_once(app_client):
    with app_client() as c:
        cont = c.app.state.container
        cont.settings.EBAY_CLIENT_ID, cont.settings.EBAY_CLIENT_SECRET = "id", "secret"
        seen = []
        items = [{"itemId": "v1|9|0", "title": "Commodore 1541 drive", "price": {"value": "45.00", "currency": "USD"},
                  "itemWebUrl": "https://www.ebay.com/itm/9"}]
        cont.shop.ebay = EbayClient(lambda: cont.settings, http=httpx.AsyncClient(transport=_ebay_transport(items, seen)))
        c.post("/api/shop/watches", json={"query": "commodore 1541", "max_price": 50})
        assert asyncio.run(cont.shop.check_watches()) == 1
        assert asyncio.run(cont.shop.check_watches()) == 0                     # announced once
        w = c.get("/api/shop/watches").json()
        assert w["deals"][0]["title"].startswith("💰 Commodore 1541 drive") and w["watches"][0]["lowest"] == 45.0


def test_repair_assistant(app_client):
    with app_client() as c:
        cont = c.app.state.container
        kb = c.get("/api/repair/kb").json()
        assert {m["id"] for m in kb["machines"]} >= {"breadbin", "c64c", "c64-ultimate", "1541"}
        r = c.post("/api/repair/diagnose", json={"machine": "breadbin", "text": "I get a black screen when I turn it on"}).json()
        assert r["symptoms"][0].startswith("Black screen") and not r["ai"]
        assert r["causes"][0]["likelihood"] == "high" and any(cz["title"].startswith("PLA") for cz in r["causes"])
        assert r["safety"] and "power supply" in r["safety"][0]["text"]               # the PSU warning
        assert any("pla" in p["tags"] for p in r["parts"]) and any("overvoltage" in p["tags"] for p in r["parts"])
        assert r["references"] and r["videos"]["searchUrl"].startswith("https://www.youtube.com/results?")
        u = c.post("/api/repair/diagnose", json={"machine": "c64-ultimate", "text": "no picture on my TV"}).json()
        assert u["symptoms"] == ["No picture over HDMI"] and not u["safety"]           # no PSU brick warning on the new one
        # 🤖 the model ranks known causes only
        cont.provider = FakeModel(cont.settings, json.dumps({
            "summary": "Likely the PLA.", "ranked": [{"ref": "black-screen#1", "why": "Classic PLA failure"},
                                                      {"ref": "invented#9", "why": "x"}],
            "questions": ["Does a Dead Test cartridge run?"], "checks": ["Feel the PLA for heat"]}))
        a = c.post("/api/repair/diagnose", json={"machine": "breadbin", "text": "black screen, the PLA gets hot"}).json()
        assert a["ai"] and a["causes"][0]["title"].startswith("PLA") and a["causes"][0]["aiWhy"] == "Classic PLA failure"
        assert all(cz["ref"] != "invented#9" for cz in a["causes"]) and a["questions"]
        assert c.post("/api/repair/diagnose", json={"machine": "toaster", "text": "x"}).status_code == 400


def test_collection(app_client):
    with app_client() as c:
        cont = c.app.state.container
        gid = _game(c, "Paradroid")
        a = c.post("/api/collection", json={"kind": "game", "title": "Paradroid", "condition": "good", "boxed": True,
                                            "value": 25, "currency": "usd"}).json()
        assert a["gameId"] == gid and a["currency"] == "USD"                    # matched to the library
        c.post(f"/api/collection/from-library/{gid}", json={"kind": "disk", "quantity": 2, "value": 5})
        c.post("/api/collection", json={"kind": "hardware", "title": "1541-II", "value": 60})
        lst = c.get("/api/collection").json()
        assert lst["summary"]["owned"] == 4 and lst["summary"]["totals"] == {"USD": 95.0}
        assert c.post("/api/collection", json={"kind": "spaceship", "title": "x"}).status_code == 400
        assert c.patch("/api/collection/999", json={"title": "x"}).status_code == 404
        # ⭐ wishlist with a target price → a price watch
        w = c.post("/api/collection", json={"kind": "cartridge", "title": "Sam's Journey", "wishlist": True,
                                            "target_price": 40}).json()
        watches = cont.shop.watches()
        assert len(watches) == 1 and watches[0]["collectionItemId"] == w["id"] and "C64" in watches[0]["query"]
        c.patch(f"/api/collection/{w['id']}", json={"wishlist": False})
        assert cont.shop.watches() == []
        csv_text = c.get("/api/collection/export.csv").text
        assert "Paradroid" in csv_text and "1541-II" in csv_text
        report = c.get("/api/collection/report").text
        assert "inventory" in report and "Sam&#x27;s Journey" in report and "95.00 USD" in report   # owned now
        c.post("/api/collection", json={"kind": "game", "title": "<script>alert(1)</script>", "value": 1})
        assert "<script>alert" not in c.get("/api/collection/report").text   # escaped
        # value guide without eBay keys → a sold-listings link, no value
        est = c.post(f"/api/collection/{a['id']}/estimate").json()
        assert est["value"] is None and "LH_Sold=1" in est["soldUrl"] and not est["configured"]


def test_finding_product_photos():
    from app.services.shop import find_product_image
    og = '<meta property="og:image" content="https://shop.example/img/p1.jpg"><img src="/logo.png">'
    assert find_product_image(og, "https://shop.example/p/1") == "https://shop.example/img/p1.jpg"
    ld = '<script type="application/ld+json">{"@type":"Product","image":["https://cdn.example/a.png"]}</script>'
    assert find_product_image(ld, "https://shop.example/p/1") == "https://cdn.example/a.png"
    rel = '<base href="https://shop.example/"><img src="assets/logo.png"><img src="assets/images/d/smart supply.jpg">'
    assert find_product_image(rel, "https://shop.example/index.php/x.html") == "https://shop.example/assets/images/d/smart%20supply.jpg"
    assert find_product_image('<img src="javascript:x.jpg"><img src="/icon.png">', "https://s.example/") is None
