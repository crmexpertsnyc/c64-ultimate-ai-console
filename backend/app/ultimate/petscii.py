"""Character, PETSCII, screen-code and REST key-name tables."""

from __future__ import annotations

# Key names accepted by POST /v1/machine:input (documented list).
REST_KEYS: frozenset[str] = frozenset(
    ["inst_del", "return", "cursor_left_right", "f7", "f1", "f3", "f5", "cursor_up_down",
     *"0123456789", *"abcdefghijklmnopqrstuvwxyz",
     "left_shift", "right_shift", "plus", "minus", "period", "colon", "at", "comma", "pound",
     "star", "semicolon", "clr_home", "equals", "arrow_up", "slash", "arrow_left", "ctrl",
     "space", "commodore", "run_stop", "restore"]
)

JOYSTICK_INPUTS: frozenset[str] = frozenset(["up", "down", "left", "right", "fire", "fire2", "fire3"])

# Friendly names used by the UI / command layer → REST key combination.
NAMED_KEYS: dict[str, tuple[str, ...]] = {
    "return": ("return",), "enter": ("return",),
    "space": ("space",),
    "run_stop": ("run_stop",), "runstop": ("run_stop",), "stop": ("run_stop",),
    "restore": ("restore",),
    "del": ("inst_del",), "delete": ("inst_del",), "backspace": ("inst_del",),
    "inst": ("left_shift", "inst_del"), "insert": ("left_shift", "inst_del"),
    "home": ("clr_home",), "clr": ("left_shift", "clr_home"), "clear": ("left_shift", "clr_home"),
    "f1": ("f1",), "f3": ("f3",), "f5": ("f5",), "f7": ("f7",),
    "f2": ("left_shift", "f1"), "f4": ("left_shift", "f3"), "f6": ("left_shift", "f5"),
    "f8": ("left_shift", "f7"),
    "cursor_down": ("cursor_up_down",), "down": ("cursor_up_down",),
    "cursor_up": ("left_shift", "cursor_up_down"), "up": ("left_shift", "cursor_up_down"),
    "cursor_right": ("cursor_left_right",), "right": ("cursor_left_right",),
    "cursor_left": ("left_shift", "cursor_left_right"), "left": ("left_shift", "cursor_left_right"),
    "commodore": ("commodore",), "cbm": ("commodore",),
    "ctrl": ("ctrl",), "control": ("ctrl",),
    "shift": ("left_shift",), "left_shift": ("left_shift",), "right_shift": ("right_shift",),
    "pound": ("pound",), "arrow_up": ("arrow_up",), "arrow_left": ("arrow_left",),
}

_SHIFTED_DIGITS = {"!": "1", '"': "2", "#": "3", "$": "4", "%": "5", "&": "6", "'": "7", "(": "8", ")": "9"}
_PUNCT = {
    "+": ("plus",), "-": ("minus",), ".": ("period",), ",": ("comma",), ":": ("colon",),
    ";": ("semicolon",), "=": ("equals",), "/": ("slash",), "@": ("at",), "*": ("star",),
    "£": ("pound",), "^": ("arrow_up",), "↑": ("arrow_up",), "_": ("arrow_left",), "←": ("arrow_left",),
    "<": ("left_shift", "comma"), ">": ("left_shift", "period"), "?": ("left_shift", "slash"),
    "[": ("left_shift", "colon"), "]": ("left_shift", "semicolon"),
    " ": ("space",), "\n": ("return",), "\r": ("return",),
}


def char_to_rest_keys(ch: str) -> tuple[str, ...] | None:
    """Map a text character to a REST key combination (C64 in default upper-case mode)."""
    if len(ch) != 1:
        return None
    lower = ch.lower()
    if "a" <= lower <= "z":
        return (lower,)  # unshifted letters are upper case in the default character set
    if ch.isdigit():
        return (ch,)
    if ch in _SHIFTED_DIGITS:
        return ("left_shift", _SHIFTED_DIGITS[ch])
    return _PUNCT.get(ch)


# --- PETSCII (keyboard buffer codes) -----------------------------------------
PETSCII_SPECIAL: dict[str, int] = {
    "return": 0x0D, "space": 0x20, "home": 0x13, "clr": 0x93, "del": 0x14, "inst": 0x94,
    "cursor_down": 0x11, "cursor_up": 0x91, "cursor_right": 0x1D, "cursor_left": 0x9D,
    "f1": 0x85, "f3": 0x86, "f5": 0x87, "f7": 0x88, "f2": 0x89, "f4": 0x8A, "f6": 0x8B, "f8": 0x8C,
}


def ascii_to_petscii(ch: str) -> int | None:
    """Map a text character to the PETSCII code the KERNAL editor expects in its buffer."""
    if ch in ("\n", "\r"):
        return 0x0D
    if ch == "£":
        return 0x5C
    if ch in ("↑", "^"):
        return 0x5E
    if ch in ("←", "_"):
        return 0x5F
    o = ord(ch)
    if 0x61 <= o <= 0x7A:  # lower-case → unshifted letter (displays upper case)
        return o - 0x20
    if 0x20 <= o <= 0x5D:
        return o
    return None


# --- Screen codes (C64 screen RAM) -----------------------------------------
def screen_code_to_char(code: int) -> str:
    c = code & 0x7F
    if c == 0x00:
        return "@"
    if 0x01 <= c <= 0x1A:
        return chr(ord("A") + c - 1)
    if c == 0x1B:
        return "["
    if c == 0x1C:
        return "£"
    if c == 0x1D:
        return "]"
    if c == 0x1E:
        return "↑"
    if c == 0x1F:
        return "←"
    if 0x20 <= c <= 0x3F:
        return chr(c)
    if c == 0x40:
        return "─"
    if c == 0x5D:
        return "│"
    return "·"  # graphics characters


def char_to_screen_code(ch: str) -> int:
    ch = ch.upper()
    if ch == "@":
        return 0
    if "A" <= ch <= "Z":
        return ord(ch) - ord("A") + 1
    o = ord(ch) if len(ch) == 1 else 0x20
    if 0x20 <= o <= 0x3F:
        return o
    return 0x20


def screen_to_text(data: bytes, cols: int = 40) -> list[str]:
    return ["".join(screen_code_to_char(b) for b in data[i:i + cols]) for i in range(0, len(data), cols)]
