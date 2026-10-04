import pytest
from pydantic import ValidationError

from app.ai.engine import CommandEngine
from app.ai.intents import Intent, IntentType
from app.ai.parser import parse_command
from app.ai.providers import AIProvider, extract_json

CASES = [
    ("Play Bruce Lee", IntentType.PLAY_GAME, {"game": "bruce lee"}),
    ("Load Summer Games", IntentType.PLAY_GAME, {"game": "summer games"}),
    ("Load Impossible Mission", IntentType.PLAY_GAME, {"game": "impossible mission"}),
    ("Mount disk 2", IntentType.MOUNT_DISK, {"disk": 2}),
    ("Put disk 2 in", IntentType.MOUNT_DISK, {"disk": 2}),
    ("insert side b", IntentType.MOUNT_DISK, {"disk": 2}),
    ("next disk", IntentType.NEXT_DISK, {}),
    ("Reset the C64", IntentType.RESET, {}),
    ("Reset the computer", IntentType.RESET, {}),
    ("reboot", IntentType.REBOOT, {}),
    ("Press fire", IntentType.JOYSTICK_INPUT, {"joystick": ["fire"], "transition": "tap"}),
    ("Press fire on joystick 2", IntentType.JOYSTICK_INPUT, {"joystick": ["fire"], "port": 2}),
    ("hold up and fire", IntentType.JOYSTICK_INPUT, {"joystick": ["up", "fire"], "transition": "press"}),
    ("Use joystick port 2", IntentType.SET_JOYSTICK_PORT, {"port": 2}),
    ("Open the Ultimate menu", IntentType.OPEN_MENU, {}),
    ("close the menu", IntentType.CLOSE_MENU, {}),
    ("Show me what is currently on the Ultimate menu", IntentType.READ_MENU, {}),
    ("menu down", IntentType.MENU_NAVIGATE, {"menu_action": "down"}),
    ("Play the SID file Commando", IntentType.PLAY_SID, {"game": "commando"}),
    ("Play Commando SID", IntentType.PLAY_SID, {"game": "commando"}),
    ("play the mod space debris", IntentType.PLAY_MOD, {"game": "space debris"}),
    ("Launch this PRG", IntentType.PLAY_GAME, {"use_selected": True}),
    ("play last ninja in the browser", IntentType.PLAY_GAME, {"game": "last ninja", "target": "browser"}),
    ("play bruce lee on my phone", IntentType.PLAY_GAME, {"game": "bruce lee", "target": "browser"}),
    ("play bruce lee here", IntentType.PLAY_GAME, {"game": "bruce lee", "target": "browser"}),
    ("play bruce lee on my c64", IntentType.PLAY_GAME, {"game": "bruce lee", "target": "c64"}),
    ("Type SYS 49152", IntentType.TYPE_TEXT, {"text": "SYS 49152", "press_return": False}),
    ("type LIST and press return", IntentType.TYPE_TEXT, {"text": "LIST", "press_return": True}),
    ("type reset", IntentType.TYPE_TEXT, {"text": "reset"}),
    ("Press return", IntentType.PRESS_KEY, {"key": "return"}),
    ("press run stop", IntentType.PRESS_KEY, {"key": "run_stop"}),
    ("press F1", IntentType.PRESS_KEY, {"key": "f1"}),
    ("What's mounted?", IntentType.SHOW_DRIVE_STATUS, {}),
    ("what's playing", IntentType.SHOW_CURRENT_GAME, {}),
    ("show device info", IntentType.SHOW_DEVICE_INFO, {}),
    ("power off", IntentType.POWER_OFF, {}),
    ("release all", IntentType.RELEASE_ALL, {}),
    ("find games by epyx", IntentType.SEARCH_GAME, {"game": "epyx"}),
    ("please play the game Elite", IntentType.PLAY_GAME, {"game": "elite"}),
    ("make me a sandwich", IntentType.UNKNOWN, {}),
    # Regressions from real use: "version" must not trigger device info inside a play command.
    ("play bubble bobble english version on the commodore 64", IntentType.PLAY_GAME,
     {"game": "bubble bobble", "variant": "english"}),
    ("/play bubble bobble usa version", IntentType.PLAY_GAME, {"game": "bubble bobble", "variant": "english"}),
    ("play bruce lee (german version)", IntentType.PLAY_GAME, {"game": "bruce lee", "variant": "german"}),
    ("play impossible mission in english", IntentType.PLAY_GAME, {"game": "impossible mission", "variant": "english"}),
    ("play reset 2", IntentType.PLAY_GAME, {"game": "reset 2"}),
    ("load disk 2", IntentType.MOUNT_DISK, {"disk": 2}),
    ("start the ultimate menu", IntentType.OPEN_MENU, {}),
    ("run stop", IntentType.PRESS_KEY, {"key": "run_stop"}),
    ("what firmware version is it", IntentType.SHOW_DEVICE_INFO, {}),
]


@pytest.mark.parametrize("text,kind,fields", CASES)
def test_rule_parser(text, kind, fields):
    intent = parse_command(text)
    assert intent.intent == kind, intent
    for k, v in fields.items():
        assert getattr(intent, k) == v, (k, getattr(intent, k))


def test_bare_direction_is_menu_only_when_menu_open():
    assert parse_command("down", {"menuOpen": True}).intent == IntentType.MENU_NAVIGATE
    assert parse_command("press down").intent == IntentType.JOYSTICK_INPUT


def test_intent_validation_rejects_bad_data():
    with pytest.raises(ValidationError):
        Intent(intent=IntentType.JOYSTICK_INPUT, joystick=["self-destruct"])
    with pytest.raises(ValidationError):
        Intent(intent=IntentType.PRESS_KEY, key="not-a-key")
    with pytest.raises(ValidationError):
        Intent(intent=IntentType.MOUNT_DISK)
    with pytest.raises(ValidationError):
        Intent.model_validate({"intent": "HTTP_REQUEST", "url": "http://x/v1/machine:writemem"})


class FakeProvider(AIProvider):
    name = "fake"

    def __init__(self, reply: str):
        self.reply = reply
        self.model = "fake-model"
        self.base_url = "http://localhost"

    @property
    def configured(self):
        return True

    async def complete(self, system, user):
        return self.reply


async def test_llm_fallback_validated():
    engine = CommandEngine(FakeProvider('```json\n{"intent": "PLAY_GAME", "game": "Bruce Lee"}\n```'))
    intent, meta = await engine.interpret("I want that karate game with the yellow jumpsuit guy")
    assert intent.intent == IntentType.PLAY_GAME and intent.source == "llm"


async def test_llm_cannot_smuggle_raw_calls():
    engine = CommandEngine(FakeProvider('{"intent": "POKE", "address": 53280, "value": 0}'))
    intent, meta = await engine.interpret("poke the border black please")
    assert intent.intent == IntentType.UNKNOWN
    assert "error" in meta["llm"]


async def test_rules_win_over_llm():
    engine = CommandEngine(FakeProvider('{"intent": "POWER_OFF"}'))
    intent, _ = await engine.interpret("Play Bruce Lee")
    assert intent.intent == IntentType.PLAY_GAME and intent.source == "rules"


def test_extract_json():
    assert extract_json('Sure! {"intent": "RESET"} done') == {"intent": "RESET"}
