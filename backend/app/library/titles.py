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
    # catalogs that strip apostrophes turn "Ghosts 'n Goblins" into "Ghostsn Goblins": put it back
    t = re.sub(r"\b([A-Za-z]{3,}s)(?:[\u00b4`'\u2019]n|[nN])(?=\s+[A-Za-z])", r"\1 'n", t)
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


# ---------------------------------------------------------------- forgiving matching
# People type "ghost and goblins"; catalogs store "Ghosts 'n Goblins", "Ghosts´n Goblins" or (apostrophe
# stripped) "Ghostsn Goblins". loose_key() maps all of those to the same key: apostrophes dropped, 'n / n /
# and / & treated alike, plurals and a leading "the" ignored, roman numerals as digits.
_APOS = "'’‘´`"
_APOS_N = re.compile(rf"\s*[{_APOS}]\s*n\b", re.I)          # "Ghosts 'n" / "Ghosts'n" → "Ghosts n"
_ROMAN_DIGITS = {"ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8"}


def _depluralize(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def loose_tokens(raw: str) -> list[str]:
    t = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", tidy_title(raw or "")).lower()
    t = re.sub(r"\s*\+\s*\d*[a-z]*\s*$", "", t)              # cracker "+4" / "+8D" trainer suffix: same game
    t = _APOS_N.sub(" n ", t)
    t = re.sub(rf"[{_APOS}]", "", t).replace("&", " and ").replace("+", " ")
    out: list[str] = []
    for i, w in enumerate(re.findall(r"[a-z0-9]+", t)):
        if i == 0 and w in ("the", "a", "an") and len(t.split()) > 1:
            continue
        if w in ("and", "n"):
            out.append("n")
        elif len(w) > 4 and w.endswith("sn"):                 # "ghostsn" (apostrophe stripped by a catalog)
            out += [_depluralize(w[:-1]), "n"]
        elif out and w in _ROMAN_DIGITS:
            out.append(_ROMAN_DIGITS[w])
        else:
            out.append(_depluralize(w))
    return out


def loose_key(raw: str) -> str:
    """Forgiving matching key: "Ghosts 'n Goblins", "ghost and goblins" and "Ghostsn Goblins" are equal."""
    return "".join(loose_tokens(raw))


def loose_score(a: str, b: str) -> float:
    """0..1 similarity of two titles on their loose keys (1.0 = same title for matching purposes)."""
    import difflib
    ka, kb = loose_key(a), loose_key(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    ta, tb = loose_tokens(a), loose_tokens(b)
    if ta and set(ta) <= set(tb):
        # every word of the request is in the title ("giana sisters" → "The Great Giana Sisters"); extra words
        # cost a little, a sequel number a little more ("Giana Sisters II")
        extra = [t for t in tb if t not in ta]
        return 0.8 + 0.15 * len(ta) / len(tb) - (0.04 if any(t.isdigit() for t in extra) else 0.0)
    if kb.startswith(ka) or ka.startswith(kb):
        return 0.7 + 0.2 * min(len(ka), len(kb)) / max(len(ka), len(kb))
    return difflib.SequenceMatcher(None, ka, kb).ratio()


def catalog_queries(title: str) -> tuple[list[str], str | None]:
    """Search strings that are safe for prefix-matching catalogs (no apostrophes: Assembly64 answers HTTP 500),
    best first, plus one broad "contains" fallback (the first significant word) to rank locally with loose_score."""
    t = re.sub(r"\s+", " ", (title or "").replace('"', " ")).strip()
    spaced = re.sub(r"\s+", " ", re.sub(rf"[{_APOS}]", " ", t)).strip()            # Ghosts n Goblins
    glued = re.sub(r"\s+", " ", re.sub(rf"\s*[{_APOS}]\s*", "", t)).strip()         # Ghostsn Goblins
    words = [w for w in re.findall(r"[A-Za-z0-9]+", spaced)]
    if words and words[0].lower() in ("the", "a", "an") and len(words) > 1:
        words = words[1:]
    andn = " ".join("n" if w.lower() in ("and", "n") else w for w in words)       # ghost n goblins
    out: list[str] = []
    for q in (spaced, glued, andn):
        if q and q.lower() not in (o.lower() for o in out):
            out.append(q)
    first = next((w for w in words if len(w) >= 3 and w.lower() not in ("and", "the")), None)
    # "%word" = names *containing* the word (Assembly64 AQL): finds "The Great Giana Sisters" for "giana sisters"
    # and "Ghostsn Goblins" for "ghost and goblins"; results are then ranked locally with loose_score
    broad = f"%{_depluralize(first.lower())}" if first else None
    return out[:3], broad
