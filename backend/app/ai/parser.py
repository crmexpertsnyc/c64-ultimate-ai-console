"""Deterministic natural-language parser (works with no AI provider)."""

from __future__ import annotations

import re
from typing import Any

from .intents import Intent, IntentType

NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                "first": 1, "second": 2, "third": 3, "fourth": 4, "a": 1, "b": 2, "c": 3, "d": 4}
SELECTED_WORDS = {"this", "it", "this prg", "this game", "this one", "that", "that game", "selected",
                  "the selected game", "this crt", "this disk", "this program", "this file"}

_KEYS = (r"return|enter|space(?:\s*bar)?|run\s*/?\s*stop|runstop|restore|f[1-8]|home|clr|clear|del|delete|"
         r"inst|insert|commodore|c=|ctrl|control|shift|cursor\s+(?:up|down|left|right)|[a-z0-9]")
_JOY = r"fire\s*[23]?|fire\s*(?:two|three)|up|down|left|right"

_PLAY_RE = re.compile(r"(?:let'?s\s+)?(?:play|load|launch|start|run|boot|fire up)\s+(?:up\s+)?(.+)")
# Things after "play/load" that are not game titles and are handled by more specific rules.
_NOT_A_TITLE = re.compile(
    r"^(?:the\s+)?(?:disk|disc|side)\b|^(?:the\s+)?(?:next|previous|other)\s+(?:disk|side)"
    r"|^(?:the\s+)?(?:sid|mod|module)\b|\b(?:sid|sid file|sid tune|sid music|mod|module|music|tune|soundtrack)$"
    r"|^(?:the\s+)?(?:ultimate\s+)?menu\b")
_VARIANT_RE = re.compile(
    r"[\s,]*[\(\[]?\b(?:the\s+|an?\s+)?(english|usa|us|uk|american|british|european|original|german|italian|french|"
    r"spanish|dutch|swedish|polish|pal|ntsc)\s+(?:version|release|edition|one)\b[\)\]]?"
    r"|\s+in\s+(english|german|italian|french|spanish)\b", re.I)
_VARIANT_ALIASES = {"usa": "english", "us": "english", "uk": "english", "american": "english", "british": "english"}
# "... in the browser" / "... here" / "... on my phone": play in the browser emulator, not on the C64.
_BROWSER_RE = re.compile(
    r"\s+(?:in\s+(?:the\s+|my\s+)?(?:browser|emulator|web\s*browser)|here|right here|"
    r"on\s+(?:this|my)\s+(?:phone|tablet|ipad|iphone|laptop|computer|pc|mac|device))$", re.I)
_ON_MACHINE_RE = re.compile(
    r"\s+(?:on|using)\s+(?:the\s+|my\s+)?(?:commodore(?:\s*64)?|c-?64|ultimate)(?:\s+ultimate)?$", re.I)

_POLITE = re.compile(r"^(?:please|hey c64|c64|ok|okay|can you|could you|would you)[,\s]+", re.I)


def _num(tok: str) -> int | None:
    tok = tok.lower().strip()
    if tok.isdigit():
        return int(tok)
    return NUMBER_WORDS.get(tok)


def _clean_title(t: str) -> str:
    t = t.strip().strip(".!?\"'")
    t = re.sub(r"^(?:the\s+)?(?:game|title)\s+", "", t, flags=re.I)
    t = _ON_MACHINE_RE.sub("", t)
    t = re.sub(r"\s+(?:for me|please|now)$", "", t, flags=re.I)
    t = _ON_MACHINE_RE.sub("", t)
    return t.strip(" ,")


def _split_variant(title: str) -> tuple[str, str | None]:
    """'bubble bobble english version' → ('bubble bobble', 'english')."""
    m = _VARIANT_RE.search(title)
    if not m:
        return title, None
    word = (m.group(1) or m.group(2)).lower()
    return (title[: m.start()] + title[m.end():]).strip(" ,"), _VARIANT_ALIASES.get(word, word)


def _norm_key(k: str) -> str:
    k = k.lower().strip()
    k = re.sub(r"\s+", " ", k)
    return {"enter": "return", "space bar": "space", "spacebar": "space", "run stop": "run_stop",
            "run/stop": "run_stop", "runstop": "run_stop", "run / stop": "run_stop", "c=": "commodore",
            "control": "ctrl", "delete": "del", "insert": "inst", "clear": "clr",
            "cursor up": "cursor_up", "cursor down": "cursor_down", "cursor left": "cursor_left",
            "cursor right": "cursor_right"}.get(k, k)


def _joy_inputs(text: str) -> list[str]:
    out = []
    for tok in re.findall(_JOY, text, flags=re.I):
        t = tok.lower().replace(" ", "")
        t = {"firetwo": "fire2", "firethree": "fire3"}.get(t, t)
        if t not in out:
            out.append(t)
    return out


def parse_command(raw: str, context: dict[str, Any] | None = None) -> Intent:
    """Map a command to an Intent. Returns UNKNOWN (confidence 0) when no rule matches."""
    context = context or {}
    text = _POLITE.sub("", raw.strip().lstrip("/").strip())
    t = text.lower().strip().rstrip(".!?")
    t = re.sub(r"\s+", " ", t)

    def make(kind: IntentType, **kw: Any) -> Intent:
        return Intent(intent=kind, source="rules", **kw)

    if not t:
        return make(IntentType.UNKNOWN, confidence=0)

    # --- safety / inputs first
    if re.fullmatch(r"(?:release|let go of|let go|stop pressing)(?: all| everything)?(?: inputs?| keys?| buttons?)?", t) \
            or t in ("panic", "release all inputs"):
        return make(IntentType.RELEASE_ALL)

    if re.fullmatch(r"run\s*/?\s*stop(?: key)?", t):
        return make(IntentType.PRESS_KEY, key="run_stop")

    # --- read TYPE early so "type reset" types the word instead of resetting
    m = re.match(r"^type\s+(.+)$", text.strip(), flags=re.I)
    if m:
        body = m.group(1).strip()
        press_return = False
        m2 = re.search(r"\s+(?:and|then)\s+(?:press|hit)\s+(?:return|enter)$|\s+(?:and|then)\s+run(?: it)?$", body, re.I)
        if m2:
            body, press_return = body[: m2.start()], True
        if len(body) >= 2 and body[0] == body[-1] and body[0] in "\"'":
            body = body[1:-1]
        return make(IntentType.TYPE_TEXT, text=body, press_return=press_return)

    # --- "play <title>" is decided before keyword rules, so "... english version" is not read as
    #     a firmware-version question and "play reset 2" plays a game called Reset 2.
    m = _PLAY_RE.fullmatch(t)
    if m and not _NOT_A_TITLE.search(_clean_title(m.group(1))):
        return _play(make, IntentType.PLAY_GAME, m.group(1))

    # --- machine
    if re.search(r"\b(?:power (?:off|down)|turn (?:it |the c64 |the ultimate )?off|shut ?down|switch off)\b", t):
        return make(IntentType.POWER_OFF)
    if re.search(r"\breboot\b|\brestart the ultimate\b|\bcold start\b", t):
        return make(IntentType.REBOOT)
    if re.search(r"\breset\b|\brestart\b", t):
        return make(IntentType.RESET)
    if re.fullmatch(r"(?:pause|freeze)(?: the)?(?: c64| computer| machine| game)?", t):
        return make(IntentType.PAUSE)
    if re.fullmatch(r"(?:resume|unpause|unfreeze|continue)(?: the)?(?: c64| computer| machine| game)?", t):
        return make(IntentType.RESUME)

    # --- menu
    if re.search(r"(?:what(?:'s| is)|read|show me what|what does).*\bmenu\b|\bmenu\b.*\b(?:say|show|contents?)\b", t):
        return make(IntentType.READ_MENU)
    if re.search(r"\b(?:close|exit|leave|hide|dismiss)\b.*\bmenu\b", t):
        return make(IntentType.CLOSE_MENU)
    if re.search(r"\b(?:open|show|enter|bring up|go to|press|start|launch|load)\b.*\bmenu\b", t) or t in ("menu", "ultimate menu"):
        return make(IntentType.OPEN_MENU)
    m = re.fullmatch(r"(?:menu\s+)?(up|down|left|right|select|enter|back|home|page up|page down|exit)(?:\s+in\s+(?:the\s+)?menu)?", t)
    if m and (t.startswith("menu") or "in the menu" in t or "in menu" in t or context.get("menuOpen")):
        action = {"select": "return", "enter": "return", "page up": "page_up", "page down": "page_down"}.get(
            m.group(1), m.group(1))
        return make(IntentType.MENU_NAVIGATE, menu_action=action)

    # --- disks
    if re.search(r"\b(?:next disk|flip (?:the )?disk|turn (?:the )?disk over|other side|swap (?:the )?disk)\b", t):
        return make(IntentType.NEXT_DISK)
    if re.search(r"\b(?:previous disk|prior disk|back (?:a|one) disk|disk back)\b", t):
        return make(IntentType.PREVIOUS_DISK)
    m = re.search(r"\b(?:disk|disc|side)\s+(\d+|one|two|three|four|five|six|a|b|c|d)\b", t)
    if m and re.search(r"\b(?:mount|insert|put|load|use|switch|change|swap|disk|side)\b", t):
        n = _num(m.group(1))
        if n:
            return make(IntentType.MOUNT_DISK, disk=n)

    # --- status questions
    if re.search(r"what(?:'s| is) mounted|drive status|which disk|what disk|what(?:'s| is) in the drive", t):
        return make(IntentType.SHOW_DRIVE_STATUS)
    if re.search(r"what(?:'s| is| am i) (?:playing|running|loaded)|current game|what game", t):
        return make(IntentType.SHOW_CURRENT_GAME)
    if re.search(r"\b(?:device|firmware|fpga|hostname|version)\b|\b(?:info|status) (?:of|about|for) the (?:c64|ultimate)\b"
                 r"|^(?:device|ultimate|c64) (?:info|status)$|^status$", t):
        return make(IntentType.SHOW_DEVICE_INFO)

    # --- joystick
    m = re.fullmatch(r"(?:use|switch to|set|select|change to)\s+(?:the\s+)?(?:joystick\s+)?(?:in\s+)?port\s*(1|2|one|two)"
                     r"|(?:use\s+)?joystick\s+(?:port\s+)?(1|2|one|two)", t)
    if m:
        return make(IntentType.SET_JOYSTICK_PORT, port=_num(m.group(1) or m.group(2)))
    m = re.fullmatch(rf"(press|hit|push|tap|hold|hold down|release|let go of)\s+(?:the\s+)?((?:{_JOY})(?:\s*(?:and|\+|,)\s*(?:{_JOY}))*)"
                     r"(?:\s+button)?(?:\s+(?:on|in)\s+(?:joystick|port|joy)\s*(?:port\s*)?(1|2|one|two))?", t)
    if m:
        verb, port = m.group(1), m.group(3)
        transition = "press" if verb.startswith("hold") else "release" if verb in ("release", "let go of") else "tap"
        return make(IntentType.JOYSTICK_INPUT, joystick=_joy_inputs(m.group(2)), transition=transition,
                    port=_num(port) if port else None)
    m = re.fullmatch(rf"(?:joystick|joy)\s*(1|2)?\s+({_JOY})", t)
    if m:
        return make(IntentType.JOYSTICK_INPUT, joystick=_joy_inputs(m.group(2)), port=_num(m.group(1)) if m.group(1) else None)

    # --- keys
    m = re.fullmatch(rf"(press|hit|tap|hold|hold down|release)\s+(?:the\s+)?({_KEYS})(?:\s+key)?", t)
    if m:
        verb = m.group(1)
        transition = "press" if verb.startswith("hold") else "release" if verb == "release" else "tap"
        return make(IntentType.PRESS_KEY, key=_norm_key(m.group(2)), transition=transition)

    # --- music
    m = re.fullmatch(r"(?:play|listen to|put on)\s+(?:the\s+)?(?:sid file|sid tune|sid music|sid)\s+(.+)", t) or \
        re.fullmatch(r"(?:play|listen to|put on)\s+(?:the\s+)?(.+?)\s+(?:sid file|sid tune|sid|music|tune|soundtrack)", t)
    if m:
        return _play(make, IntentType.PLAY_SID, m.group(1))
    m = re.fullmatch(r"(?:play|listen to)\s+(?:the\s+)?(?:mod file|module|mod)\s+(.+)", t) or \
        re.fullmatch(r"(?:play|listen to)\s+(?:the\s+)?(.+?)\s+(?:mod|module)", t)
    if m:
        return _play(make, IntentType.PLAY_MOD, m.group(1))

    # --- questions (answered by the AI, with web search when configured) — not machine actions
    if _is_question(t) or raw.strip().endswith("?"):
        return make(IntentType.ASK, text=re.sub(r"^(?:ask|question)\s*[:,]?\s*", "", raw.strip(), flags=re.I)[:200])

    # --- search / play
    m = re.fullmatch(r"(?:search|find|look up|look for|do i have|show me|list)\s+(?:for\s+)?(?:games?\s+(?:by|from|called|named)\s+)?(.+)", t)
    if m:
        return make(IntentType.SEARCH_GAME, game=_clean_title(m.group(1)))
    m = re.fullmatch(r"(?:let'?s\s+)?(?:play|load|launch|start|run|boot|fire up)\s+(?:up\s+)?(.+)", t)
    if m:
        return _play(make, IntentType.PLAY_GAME, m.group(1))
    return make(IntentType.UNKNOWN, confidence=0)


_QUESTION_START = re.compile(
    r"^(?:ask|question|what|which|who|whom|whose|why|how|when|where|is|are|was|were|does|do|did|should|would|"
    r"could you|can you|tell me|research|recommend|suggest|explain|compare|describe|give me|teach me|help me choose|"
    r"i want to know|what's the best|whats the best)\b")
_SUPERLATIVE = re.compile(r"\b(?:best|top\s*\d*|greatest|most popular|popular|favou?rite|recommended|classic|underrated|hidden gems?)\b")


def _is_question(t: str) -> bool:
    """Questions / research requests. Machine commands ("what's playing", "what's mounted") are matched
    by earlier rules; "find the best racing games" is a question, "find bruce lee" is a search."""
    if _QUESTION_START.match(t):
        return True
    return bool(re.match(r"(?:search|find|look up|look for|show me|list)\b", t) and _SUPERLATIVE.search(t))


def _play(make, kind: IntentType, title: str) -> Intent:  # noqa: ANN001
    target = "c64"
    stripped = _BROWSER_RE.sub("", title.strip().strip(".!?"))
    if stripped != title.strip().strip(".!?"):
        title, target = stripped, "browser"
    title, variant = _split_variant(_clean_title(title))
    title = _clean_title(title)
    if title.lower() in SELECTED_WORDS:
        return make(kind, use_selected=True, variant=variant, target=target)
    return make(kind, game=title, variant=variant, target=target)
