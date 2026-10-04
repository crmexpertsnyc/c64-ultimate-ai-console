"""The validated intent schema — the only thing the LLM (or the rule parser) may produce.

An intent is data, not code: the command router maps each intent type to one approved
service method. There is no intent that carries a URL, an HTTP method, a memory
address or raw bytes.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.ultimate.input import resolve_key
from app.ultimate.menu import MENU_KEYS
from app.ultimate.petscii import JOYSTICK_INPUTS


class IntentType(str, Enum):
    PLAY_GAME = "PLAY_GAME"
    SEARCH_GAME = "SEARCH_GAME"
    MOUNT_DISK = "MOUNT_DISK"
    NEXT_DISK = "NEXT_DISK"
    PREVIOUS_DISK = "PREVIOUS_DISK"
    RESET = "RESET"
    REBOOT = "REBOOT"
    POWER_OFF = "POWER_OFF"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    OPEN_MENU = "OPEN_MENU"
    CLOSE_MENU = "CLOSE_MENU"
    READ_MENU = "READ_MENU"
    MENU_NAVIGATE = "MENU_NAVIGATE"
    PRESS_KEY = "PRESS_KEY"
    TYPE_TEXT = "TYPE_TEXT"
    JOYSTICK_INPUT = "JOYSTICK_INPUT"
    SET_JOYSTICK_PORT = "SET_JOYSTICK_PORT"
    RELEASE_ALL = "RELEASE_ALL"
    PLAY_SID = "PLAY_SID"
    PLAY_MOD = "PLAY_MOD"
    SHOW_DEVICE_INFO = "SHOW_DEVICE_INFO"
    SHOW_CURRENT_GAME = "SHOW_CURRENT_GAME"
    SHOW_DRIVE_STATUS = "SHOW_DRIVE_STATUS"
    ASK = "ASK"  # a question to answer (optionally with web search), not a machine action
    UNKNOWN = "UNKNOWN"


class Intent(BaseModel):
    intent: IntentType
    game: str | None = Field(None, max_length=120, description="title to play/search")
    disk: int | None = Field(None, ge=1, le=20)
    key: str | None = Field(None, max_length=20)
    text: str | None = Field(None, max_length=200)
    press_return: bool = False
    joystick: list[str] | None = None
    port: int | None = Field(None, ge=1, le=2)
    transition: Literal["tap", "press", "release"] = "tap"
    menu_action: str | None = None
    variant: str | None = Field(None, max_length=30, description="preferred release: english, german, original, …")
    use_selected: bool = False  # "this PRG" / "it" → the game selected in the UI
    target: Literal["c64", "browser"] = Field(
        "c64", description="where to play: c64 = the real C64 Ultimate, browser = the emulator on the user's device")
    confidence: float = Field(1.0, ge=0, le=1)
    source: Literal["rules", "llm"] = "rules"
    reason: str | None = Field(None, max_length=300)

    @field_validator("key")
    @classmethod
    def _key(cls, v: str | None) -> str | None:
        if v is not None:
            resolve_key(v)  # raises ValueError for unknown keys
        return v

    @field_validator("joystick")
    @classmethod
    def _joy(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        v = [x.lower().strip() for x in v]
        bad = [x for x in v if x not in JOYSTICK_INPUTS]
        if bad or not v or len(v) > 7:
            raise ValueError(f"invalid joystick inputs {bad}")
        return v

    @field_validator("menu_action")
    @classmethod
    def _menu(cls, v: str | None) -> str | None:
        if v is not None and v not in MENU_KEYS:
            raise ValueError(f"menu_action must be one of {sorted(MENU_KEYS)}")
        return v

    @model_validator(mode="after")
    def _required(self) -> Intent:
        need = {
            IntentType.SEARCH_GAME: "game", IntentType.MOUNT_DISK: "disk", IntentType.PRESS_KEY: "key",
            IntentType.TYPE_TEXT: "text", IntentType.ASK: "text", IntentType.JOYSTICK_INPUT: "joystick",
            IntentType.SET_JOYSTICK_PORT: "port", IntentType.MENU_NAVIGATE: "menu_action",
        }
        f = need.get(self.intent)
        if f and getattr(self, f) in (None, "", []):
            raise ValueError(f"{self.intent.value} requires '{f}'")
        if self.intent in (IntentType.PLAY_GAME, IntentType.PLAY_SID, IntentType.PLAY_MOD) and not (
                self.game or self.use_selected):
            raise ValueError(f"{self.intent.value} requires 'game' or use_selected")
        return self


INTENT_HELP = {
    IntentType.PLAY_GAME: "Play Bruce Lee",
    IntentType.SEARCH_GAME: "Find games by Epyx",
    IntentType.MOUNT_DISK: "Put disk 2 in",
    IntentType.NEXT_DISK: "Next disk",
    IntentType.PREVIOUS_DISK: "Previous disk",
    IntentType.RESET: "Reset the C64",
    IntentType.REBOOT: "Reboot the Ultimate",
    IntentType.POWER_OFF: "Power off (asks for confirmation)",
    IntentType.OPEN_MENU: "Open the Ultimate menu",
    IntentType.CLOSE_MENU: "Close the menu",
    IntentType.READ_MENU: "Show me what is on the Ultimate menu",
    IntentType.MENU_NAVIGATE: "Menu down",
    IntentType.PRESS_KEY: "Press return",
    IntentType.TYPE_TEXT: "Type SYS 49152 and press return",
    IntentType.JOYSTICK_INPUT: "Press fire on joystick 2",
    IntentType.SET_JOYSTICK_PORT: "Use joystick port 1",
    IntentType.RELEASE_ALL: "Release all inputs",
    IntentType.PLAY_SID: "Play the SID file Commando",
    IntentType.PLAY_MOD: "Play the MOD Space Debris",
    IntentType.SHOW_DEVICE_INFO: "Show device info",
    IntentType.SHOW_CURRENT_GAME: "What's playing?",
    IntentType.SHOW_DRIVE_STATUS: "What's mounted?",
    IntentType.ASK: "What are the best C64 platform games?",
}
