import json

import pytest

from app.ai.providers import AIProvider
from app.services import ask as ask_mod


class FakeModel(AIProvider):
    name = "fake"
    model = "fake-120b"

    def __init__(self, settings, reply):  # noqa: ANN001
        super().__init__(settings)
        self.reply = reply
        self.calls = []

    @property
    def configured(self) -> bool:
        return True

    async def complete(self, system, user, max_tokens=None, timeout=None):  # noqa: ANN001
        self.calls.append((system, user, max_tokens))
        return self.reply


RESULTS = [{"title": "Top 100 C64 games", "url": "https://example.test/top", "description": "Impossible Mission, Bruce Lee"},
           {"title": "Lemon64 top", "url": "https://example.test/lemon", "description": "The Last Ninja"}]


def _setup(c, reply, key="test-key"):
    cont = c.app.state.container
    fake = FakeModel(cont.settings, json.dumps(reply))
    cont.provider = fake
    cont.config.update({"BRAVE_API_KEY": key})
    from app.models.db import Game
    with cont.sf() as s:
        s.add(Game(group_key="t:bruce", title="Bruce Lee", normalized_title="brucelee", format="t64"))
        s.commit()
    return fake


def test_question_is_answered_with_web_sources_and_playable_games(app_client, monkeypatch):
    seen = {}

    async def fake_search(query, key, count=8, timeout=10):  # noqa: ANN001
        seen.setdefault("query", query)  # the first search is the question itself
        seen["key"] = key
        seen.setdefault("all", []).append(query)
        return RESULTS

    monkeypatch.setattr(ask_mod, "brave_search", fake_search)
    with app_client() as c:
        fake = _setup(c, {"answer": "Classics include **Bruce Lee** [1] and The Last Ninja [2].",
                          "games": ["Bruce Lee", "The Last Ninja"], "sources": [1, 2]})
        r = c.post("/api/command", json={"text": "research the best games for the commodore 64 and tell me them"}).json()
        assert r["ok"] and r["intent"]["intent"] == "ASK", r
        a = r["data"]["ask"]
        assert "Bruce Lee" in a["answer"] and a["webSearch"] is True
        assert [s["url"] for s in a["sources"]] == ["https://example.test/top", "https://example.test/lemon"]
        assert a["games"][0]["gameId"] and a["games"][0]["title"] == "Bruce Lee" and a["games"][0]["browserOk"]
        assert a["games"][1]["gameId"] is None  # not in the library (catalog not configured in this test)
        assert seen["key"] == "test-key" and "commodore" in seen["query"].lower()
        # A game in neither the library nor the catalog is also looked up on itch.io / CSDb / Lemon64.
        assert any("site:itch.io" in q for q in seen["all"])
        system, user, max_tokens = fake.calls[0]
        assert "[1] Top 100 C64 games" in user and max_tokens == 2500
        # Asking never touches the machine.
        assert c.get("/api/current-session").json()["session"]["gameId"] is None


def test_search_failure_still_answers_from_the_model(app_client, monkeypatch):
    async def broken(query, key, count=8, timeout=10):  # noqa: ANN001
        raise ask_mod.AskError("Brave Search rejected the API key")

    monkeypatch.setattr(ask_mod, "brave_search", broken)
    with app_client() as c:
        _setup(c, {"answer": "From memory: Impossible Mission.", "games": ["Impossible Mission"], "sources": []})
        a = c.post("/api/ask", json={"question": "what is a great c64 game?"}).json()
        assert a["webSearch"] is False and a["searchNote"] == "Brave Search rejected the API key"
        assert a["answer"].startswith("From memory")


def test_no_key_means_no_search(app_client, monkeypatch):
    async def must_not_run(*a, **k):  # noqa: ANN001, ANN002, ANN003
        raise AssertionError("search without a key")

    monkeypatch.setattr(ask_mod, "brave_search", must_not_run)
    with app_client() as c:
        _setup(c, {"answer": "ok", "games": []}, key="")
        assert c.post("/api/ask", json={"question": "who made bruce lee?"}).json()["webSearch"] is False


def test_model_ignoring_json_still_shows_text(app_client, monkeypatch):
    monkeypatch.setattr(ask_mod, "brave_search", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    with app_client() as c:
        cont = c.app.state.container
        cont.provider = FakeModel(cont.settings, "Just plain text about the SID chip.")
        a = c.post("/api/ask", json={"question": "what is the sid chip?"}).json()
        assert a["answer"] == "Just plain text about the SID chip." and a["games"] == []


def test_ask_without_ai_model(app_client):
    with app_client() as c:
        r = c.post("/api/ask", json={"question": "what is the best game?"})
        assert r.status_code == 409 and "AI model" in r.json()["detail"]


@pytest.mark.parametrize("text", ["what's mounted?", "play bruce lee", "find bruce lee"])
def test_commands_are_not_questions(text):
    from app.ai.parser import parse_command
    assert parse_command(text).intent.value != "ASK"
