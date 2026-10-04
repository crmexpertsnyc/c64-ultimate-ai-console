# ruff: noqa: E501  (sample pages are kept as the sites send them)
import pytest

from app.services.firmware_watch import (
    changelog_summary,
    is_newer,
    parse_commodore,
    parse_ultimate64,
    source_for,
    version_key,
)

CBM_PAGE = """<html><body>
<a href="https://downloads.commodore-international.com/documentation/C64U/c64u-user-guide-1st-edition.pdf">Guide</a>
<a href="https://downloads.commodore.net/firmware/c64u_v1.0.9.zip">Old</a>
<a class="btn" href="https://downloads.commodore.net/firmware/c64u_v1.1.0.zip">Download firmware 1.1.0</a>
<a href="https://downloads.commodore-international.com/documentation/changelog-1.1.0.txt">Changelog</a>
</body></html>"""
CBM_NEW = CBM_PAGE.replace("c64u_v1.1.0.zip", "c64u_v1.2.0.zip").replace("changelog-1.1.0.txt", "changelog-1.2.0.txt")
CHANGELOG = """** 1.2.0 (build 201) - October 1, 2026 **

Added
-----

- Faster disk loading.
- New SID filter options.
"""
U64_PAGE = """<h3>Firmware version 3.15a - for All Platforms: U2, U2+, U2+L, U64 and U64E2! - Dated 2026-09-11</h3><p>Fixes</p>
<h3>Firmware version 3.14d - for All Platforms: U2, U2+, U2+L, U64 and U64E2! - Dated 2026-03-01</h3>
<h3>Firmware version 3.11 / Core 1.43 - Dated 2023-12-28</h3>
<h3>Firmware version 3.10 / Core 1.41 - Dated: 2021-07-24</h3>"""


@pytest.mark.parametrize(("latest", "installed", "newer"), [
    ("1.1.0", "1.1.0s2", False),          # suffix stripped: equal = current
    ("1.2.0", "1.1.0s2", True),
    ("1.1.0", "1.2.0", False),
    ("1.10.0", "1.9.9", True),            # numeric, not alphabetical
    ("1.1", "1.1.0", False),
    ("1.1.1", "1.1", True),
    ("3.15a", "3.15", True),              # Gideon's letter revisions
    ("3.15a", "3.14d", True),
    ("3.14d", "3.15", False),
    ("3.15a", "3.15a", False),
    ("1.2.0", "", False),                 # unknown installed version: no notice
    ("", "1.1.0", False),
])
def test_version_comparison(latest, installed, newer):
    assert is_newer(latest, installed) is newer


def test_version_key_and_products():
    assert version_key("1.1.0s2") == version_key("1.1.0") == version_key("v1.1")
    assert version_key("garbage") == ()
    assert source_for("C64 Ultimate") == "commodore"
    assert source_for("Ultimate 64 Elite") == "ultimate64" and source_for("Ultimate-II+") == "ultimate64"
    assert source_for("U64E2") == "ultimate64" and source_for(None) == "commodore"


def test_parsing_the_download_pages():
    assert parse_commodore(CBM_PAGE) == {"version": "1.1.0",
                                         "changelogUrl": "https://downloads.commodore-international.com/documentation/changelog-1.1.0.txt"}
    evil = CBM_NEW.replace("downloads.commodore-international.com", "evil.example")
    assert parse_commodore(evil)["changelogUrl"] is None                     # changelog only from the maker's sites
    assert parse_commodore("<html>nothing</html>") is None
    u = parse_ultimate64(U64_PAGE)
    assert u["version"] == "3.15a" and u["dated"] == "2026-09-11" and "U64E2" in u["platforms"]
    assert parse_ultimate64("<p>no versions</p>") is None
    assert changelog_summary(CHANGELOG) == "1.2.0 (build 201) - October 1, 2026 Added - Faster disk loading. - New SID filter options."


def _fake(pages, seen):
    async def fetch(url):
        seen.append(url)
        if url in pages:
            return pages[url]
        raise OSError("offline")
    return fetch


def test_firmware_notice_created_once(app_client):
    seen: list[str] = []
    pages = {"https://commodore.net/downloads": CBM_NEW,
             "https://downloads.commodore-international.com/documentation/changelog-1.2.0.txt": CHANGELOG}
    with app_client() as c:
        fw = c.app.state.container.firmware
        fw._fetch = _fake(pages, seen)
        fw.device_info = lambda: ("C64 Ultimate", "1.1.0s2")
        before = c.get("/api/firmware").json()
        assert before["newer"] is False and before["latest"] is None and before["installed"] == "1.1.0s2"
        st = c.post("/api/firmware/check").json()
        assert st == {**st, "product": "C64 Ultimate", "installed": "1.1.0s2", "latest": "1.2.0", "newer": True,
                      "downloadPage": "https://commodore.net/downloads", "source": "commodore",
                      "changelogUrl": "https://downloads.commodore-international.com/documentation/changelog-1.2.0.txt"}
        assert st["checkedAt"] and "manual step" in st["note"]
        c.post("/api/firmware/check")
        c.post("/api/firmware/check")
        news = c.get("/api/news", params={"kind": "news"}).json()["items"]
        fwnews = [n for n in news if n["source"] == "firmware"]
        assert len(fwnews) == 1                                                       # once, however often it's checked
        n = fwnews[0]
        assert n["title"] == "C64 Ultimate firmware 1.2.0 is out" and n["url"] == "https://commodore.net/downloads"
        assert "Faster disk loading" in n["summary"] and "manual step" in n["summary"]
        # only the page and the plain-text changelog were read — never a firmware file
        assert not any(u.endswith(".zip") for u in seen)
        assert seen.count("https://commodore.net/downloads") == 3
        # after updating the device, the notice goes away
        fw.device_info = lambda: ("C64 Ultimate", "1.2.0")
        assert c.get("/api/firmware").json()["newer"] is False


def test_current_device_gets_no_notice_and_offline_is_reported(app_client):
    with app_client() as c:
        fw = c.app.state.container.firmware
        fw.device_info = lambda: ("C64 Ultimate", "1.1.0s2")
        fw._fetch = _fake({"https://commodore.net/downloads": CBM_PAGE}, [])
        st = c.post("/api/firmware/check").json()
        assert st["latest"] == "1.1.0" and st["newer"] is False
        assert not [n for n in c.get("/api/news").json()["items"] if n["source"] == "firmware"]
        fw._fetch = _fake({}, [])
        st = c.post("/api/firmware/check").json()
        assert st["error"] and st["latest"] == "1.1.0"                                # last known kept
        # an Ultimate 64 is compared with ultimate64.com
        fw.device_info = lambda: ("Ultimate 64 Elite", "3.14d")
        fw._fetch = _fake({"https://ultimate64.com/Firmware": U64_PAGE}, [])
        st = c.post("/api/firmware/check").json()
        assert st["source"] == "ultimate64" and st["latest"] == "3.15a" and st["newer"] is True
        titles = [n["title"] for n in c.get("/api/news").json()["items"] if n["source"] == "firmware"]
        assert titles == ["Ultimate 64 Elite firmware 3.15a is out"]
