"""Readable titles from catalog / Gamebase / file names.

    "last ninja_ the (by $olo1870) [easyflash]"  → "The Last Ninja (EasyFlash)"
    "Bruce Lee_ Return of Fury"                  → "Bruce Lee: Return of Fury"
    "maniac mansion"                             → "Maniac Mansion"
    "Last Ninja II"                              → "Last Ninja II"   (already fine: unchanged)

``title_key`` is the matching key: the tidy title without edition tags, so "The Last Ninja (EasyFlash)"
and "Last Ninja_ The" both match a request for "last ninja".
"""

from __future__ import annotations

import re

from .scanner import normalize_key

# Bracket tags worth keeping as an edition suffix; everything else in [...] is dropped.
KEEP_TAGS = {"easyflash": "EasyFlash", "ef": "EasyFlash", "crt": "Cartridge", "preview": "Preview",
             "ntsc": "NTSC", "pal": "PAL"}
ARTICLES = ("the", "a", "an", "der", "die", "das", "le", "la", "les", "el", "il")
SMALL = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "nor", "of", "on", "or", "the", "to", "vs", "with"}
ROMAN = re.compile(r"^(?:i{1,3}|iv|v|vi{0,3}|ix|x)$", re.I)


def _title_case(text: str) -> str:
    words = text.split(" ")
    out = []
    for n, w in enumerate(words):
        lw = w.lower()
        if ROMAN.match(lw) and n:
            out.append(lw.upper())
        elif n and lw in SMALL:
            out.append(lw)
        else:
            out.append(lw[:1].upper() + lw[1:])
    return " ".join(out)


def tidy_title(raw: str) -> str:
    t = (raw or "").strip()
    if not t:
        return t
    tags: list[str] = []
    for tag in re.findall(r"\[([^\]]*)\]", t):
        keep = KEEP_TAGS.get(tag.strip().lower())
        if keep and keep not in tags:
            tags.append(keep)
    t = re.sub(r"\[[^\]]*\]", " ", t)
    t = re.sub(r"\(\s*by\s+[^)]*\)", " ", t, flags=re.I)        # "(by $olo1870)" credits
    t = re.sub(r"\s+", " ", t).strip(" _-")
    # "Last Ninja_ The" / "Last Ninja, The" → "The Last Ninja"
    m = re.match(rf"^(.*?)[_,]\s*({'|'.join(ARTICLES)})$", t, flags=re.I)
    if m:
        t = f"{m.group(2)} {m.group(1).strip()}"
    t = re.sub(r"_\s+", ": ", t)                                 # Gamebase writes ":" as "_ "
    t = t.replace("_", " ")
    t = re.sub(r"\s+", " ", t).strip()
    if t == t.lower():                                           # only fix titles with no capitals at all
        t = _title_case(t)
    elif t.split(" ")[0][:1].islower():
        t = t[:1].upper() + t[1:]
    return t + "".join(f" ({tag})" for tag in tags)


def title_key(raw: str) -> str:
    """Matching key: tidy title without (...) edition tags or a leading article."""
    t = re.sub(r"\([^)]*\)", " ", tidy_title(raw))
    return normalize_key(t)


def looks_untidy(title: str) -> bool:
    return bool(title) and (title == title.lower() or "_" in title or "[" in title or "(by " in title.lower())
