"""Forgiving title search: "ghost and goblins" finds "Ghosts 'n Goblins" in the library and online."""

from __future__ import annotations

import asyncio

import pytest
from test_catalog import ENTRIES, FILES, SEARCH, fake_catalog, make_d64  # noqa: F401 - fixture import

from app.library.titles import catalog_queries, loose_key, loose_score
from app.services.assembly64 import build_query


@pytest.mark.parametrize("a", ["Ghosts 'n Goblins", "ghost and goblins", "Ghostsn Goblins", "Ghosts´n Goblins",
                               "GhostsN Goblins", "Ghosts n Goblins", "Ghosts & Goblins", "ghosts 'n' goblins"])
def test_spellings_share_a_key(a):
    assert loose_key(a) == loose_key("Ghosts 'n Goblins")


def test_loose_key_details():
    assert loose_key("The Last Ninja II") == loose_key("last ninja 2")
    assert loose_key("Boulder Dash") != loose_key("Boulder Dash II")
    assert loose_key("The") == "the"                         # a lone article is kept


def test_scores_rank_the_real_title_first():
    q = "giana sisters"
    assert loose_score(q, "The Great Giana Sisters") > loose_score(q, "Giana Sisters II")
    assert loose_score("ghost and goblins", "Ghostsn Goblins") == 1.0
    assert loose_score("ghost and goblins", "Ghostsn Goblins II") < 1.0
    assert loose_score("bruce lee", "Boulder Dash") < 0.5


def test_catalog_queries_never_contain_apostrophes():
    qs, broad = catalog_queries("Ghosts 'n Goblins")
    assert qs == ["Ghosts n Goblins", "Ghostsn Goblins"] and broad == "%ghost"
    assert all("'" not in q for q in qs)
    qs, broad = catalog_queries("ghost and goblins")
    assert "ghost n goblins" in qs and broad == "%ghost"
    assert "'" not in build_query("Ghosts 'n Goblins", "games")       # the catalog answers HTTP 500 otherwise
    assert build_query("%giana", None) == '(name:"%giana")'           # "%x" = contains x
    assert build_query("50% off", None) == '(name:"50 off")'


@pytest.fixture
def ghosts(monkeypatch):
    monkeypatch.setitem(SEARCH, "%ghost", [
        {"category": 16, "id": "3002", "name": "Ghosts"},
        {"category": 16, "id": "3001", "name": "Ghostsn Goblins II"},
        {"category": 0, "id": "3003", "name": "Ghostsn Goblins +4"},
        {"category": 16, "id": "3000", "name": "Ghostsn Goblins"},
    ])
    monkeypatch.setitem(SEARCH, "%giana", [
        {"category": 16, "id": "4001", "name": "Giana Sisters II"},
        {"category": 16, "id": "4000", "name": "Great Giana Sisters _ The"},
    ])
    monkeypatch.setitem(ENTRIES, "3000/16", [{"id": 0, "path": "GNG.D64", "size": 174848}])
    monkeypatch.setitem(FILES, "GNG.D64", make_d64("GHOSTS N GOBLINS"))


def test_find_for_play_handles_and_and_apostrophes(app_client, fake_catalog, ghosts):  # noqa: F811
    with app_client(ASSEMBLY64_URL="https://example.test", ASSEMBLY64_CLIENT_ID="Spiffy") as c:
        cat = c.app.state.container.catalog
        for q in ("ghost and goblins", "Ghosts 'n Goblins", "ghosts n goblins"):
            found = asyncio.run(cat.find_for_play(q, "games"))
            assert found[0]["id"] == "3000", (q, found[:3])                   # the clean original first
            assert found[1]["name"] == "Ghostsn Goblins +4"                   # same game, trainer version after
        giana = asyncio.run(cat.find_for_play("giana sisters", "games"))
        assert giana[0]["id"] == "4000"
        assert [r["name"] for r in asyncio.run(cat.search("ghost and goblins"))][:1] == ["Ghostsn Goblins"]


def test_play_command_finds_it_online(app_client, fake_catalog, ghosts):  # noqa: F811
    with app_client(ASSEMBLY64_URL="https://example.test", ASSEMBLY64_CLIENT_ID="Spiffy") as c:
        r = c.post("/api/command", json={"text": "play ghost and goblins"}).json()
        assert r["ok"], r
        assert "Ghostsn Goblins" in r["message"] or "Ghosts" in r["message"]


def test_library_matches_other_spellings(app_client):
    with app_client() as c:
        from app.library.repository import LibraryRepository
        from app.models.db import Game
        sf = c.app.state.container.sf
        with sf() as s:
            s.add(Game(group_key="ghostsngoblins", title="Ghosts 'n Goblins", normalized_title="ghostsngoblins",
                       format="d64", category="game"))
            s.commit()
            repo = LibraryRepository(s)
            best = repo.find_best("ghost and goblins")
            assert best and best[0][0].title == "Ghosts 'n Goblins" and best[0][1] >= 0.9
            games, total = repo.search("ghost and goblins")
            assert total == 1 and games[0].title == "Ghosts 'n Goblins"


def test_tidy_title_restores_n():
    from app.library.titles import tidy_title
    assert tidy_title("Ghostsn Goblins") == "Ghosts 'n Goblins"
    assert tidy_title("GhostsN Goblins II") == "Ghosts 'n Goblins II"
    for keep in ("Lemmings", "Kids Play", "Dragons Lair", "Ghosts n Goblins"):
        assert tidy_title(keep) == keep
