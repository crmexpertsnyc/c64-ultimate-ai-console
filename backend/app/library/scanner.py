"""Recursive library import.

* Walks one or more directories (local paths, mounted network shares, UNC paths).
* Identifies C64 media by extension and reads headers for metadata (read-only).
* Normalises filenames and groups multi-disk sets:
      "Summer Games Disk 1.d64" + "Summer Games Disk 2.d64" → one game, two disks
      "Last Ninja (1987)(System 3)(Side A).d64" → title, year, publisher, disk 1
* Never renames, moves, modifies or deletes source files.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.db import Game, LibraryRoot, Media

from .media import DiskImage, crt_header, mod_header, sid_header, t64_entries

log = logging.getLogger("c64.library.scanner")

EXTENSIONS = {".d64": "d64", ".d71": "d71", ".d81": "d81", ".g64": "g64", ".g71": "g71", ".prg": "prg",
              ".crt": "crt", ".sid": "sid", ".mod": "mod", ".t64": "t64"}
DISK_FORMATS = {"d64", "d71", "d81", "g64", "g71"}
CATEGORY_BY_FORMAT = {"sid": "music", "mod": "music"}
MAX_PARSE_BYTES = 2 * 1024 * 1024

_DISK_RE = re.compile(
    r"""[\s_\-.]*[\(\[]?\s*
        \b(?:disk|disc|side|dsk|d)\s*[_\-]?\s*
        (?P<num>\d{1,2}|[a-hA-H])
        (?:\s*(?:of|/)\s*(?P<total>\d{1,2}))?
        \s*[\)\]]?\s*$""",
    re.IGNORECASE | re.VERBOSE,
)
_YEAR_RE = re.compile(r"\((19[78]\d|20[0-2]\d)\)")
_PAREN_RE = re.compile(r"\(([^()]*)\)")
_BRACKET_RE = re.compile(r"\[[^\]]*\]")


@dataclass
class ParsedName:
    title: str
    normalized: str
    disk_number: int = 1
    disk_total: int | None = None
    disk_label: str | None = None
    year: int | None = None
    publisher: str | None = None
    tags: list[str] = field(default_factory=list)


def normalize_key(title: str) -> str:
    t = title.lower()
    t = re.sub(r"^the\s+", "", t)
    t = t.replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "", t)


def parse_filename(filename: str) -> ParsedName:
    stem = Path(filename).stem
    tags = [b.strip("[] ") for b in _BRACKET_RE.findall(stem)]
    stem = _BRACKET_RE.sub(" ", stem)
    year = None
    publisher = None
    m = _YEAR_RE.search(stem)
    if m:
        year = int(m.group(1))
        after = stem[m.end():]
        pm = _PAREN_RE.match(after.strip())
        if pm and not _DISK_RE.fullmatch("(" + pm.group(1) + ")"):
            publisher = pm.group(1).strip() or None

    disk_number, disk_total, disk_label = 1, None, None
    # Disk markers may be inside parentheses anywhere, e.g. "(Disk 1 of 2)" / "(Side B)".
    for group in _PAREN_RE.findall(stem):
        dm = _DISK_RE.fullmatch("(" + group + ")")
        if dm:
            disk_number, disk_total, disk_label = _disk_num(dm.group("num")), _int(dm.group("total")), group
            stem = stem.replace("(" + group + ")", " ")
            break
    else:
        dm = _DISK_RE.search(stem.replace("_", " "))
        if dm and dm.start() > 0:
            disk_number, disk_total = _disk_num(dm.group("num")), _int(dm.group("total"))
            disk_label = dm.group(0).strip(" _-.()[]")
            stem = stem.replace("_", " ")[: dm.start()]

    # Remove remaining TOSEC-style parenthesised metadata from the display title.
    title = _PAREN_RE.sub(" ", stem)
    title = re.sub(r"[_]+", " ", title)
    title = re.sub(r"(?<=[a-z])\.(?=[A-Za-z])", " ", title)
    title = re.sub(r"\s+", " ", title).strip(" -_.")
    # TOSEC puts articles last: "Last Ninja, The" → "The Last Ninja".
    m_article = re.match(r"^(.*),\s*(The|A|An|Der|Die|Das|Le|La|Les)$", title, flags=re.I)
    if m_article:
        title = f"{m_article.group(2).capitalize()} {m_article.group(1)}"
    if title.isupper() or title.islower():
        title = " ".join(w.capitalize() if len(w) > 2 or i == 0 else w for i, w in enumerate(title.lower().split()))
    return ParsedName(title=title or Path(filename).stem, normalized=normalize_key(title or stem),
                      disk_number=disk_number, disk_total=disk_total, disk_label=disk_label,
                      year=year, publisher=publisher, tags=tags)


def _int(v: str | None) -> int | None:
    return int(v) if v and v.isdigit() else None


def _disk_num(v: str) -> int:
    return int(v) if v.isdigit() else ord(v.lower()) - ord("a") + 1


def iter_media(root: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if Path(name).suffix.lower() in EXTENSIONS:
                yield Path(dirpath) / name


def inspect_file(path: Path, fmt: str) -> dict:
    """Header metadata; errors are recorded, never raised."""
    try:
        if path.stat().st_size > MAX_PARSE_BYTES and fmt not in ("mod",):
            return {"note": "large file; header not parsed"}
        with path.open("rb") as fh:
            data = fh.read(MAX_PARSE_BYTES)
        if fmt in ("d64", "d71", "d81"):
            disk = DiskImage(data, fmt)
            directory = disk.directory()
            boot = disk.boot_entry()
            return {"diskName": disk.disk_name,
                    "directory": [{"name": e.name, "type": e.type, "blocks": e.blocks} for e in directory[:64]],
                    "boot": boot.name if boot else None,
                    "bootIsFirst": bool(boot and directory and directory[0].name == boot.name)}
        if fmt == "sid":
            return sid_header(data)
        if fmt == "crt":
            return crt_header(data)
        if fmt == "mod":
            return mod_header(data)
        if fmt == "t64":
            return {"entries": [{"name": e["name"], "start": e["start"]} for e in t64_entries(data)[:32]]}
        if fmt == "prg" and len(data) >= 2:
            return {"loadAddress": f"${data[0] | (data[1] << 8):04X}", "size": len(data)}
    except (OSError, ValueError, IndexError) as exc:
        return {"parseError": str(exc)}
    return {}


@dataclass
class ScanResult:
    root: str
    files_found: int = 0
    games_created: int = 0
    media_added: int = 0
    media_updated: int = 0
    missing: int = 0
    errors: list[str] = field(default_factory=list)


def scan_root(session: Session, root_path: str, progress=None) -> ScanResult:  # noqa: ANN001
    root = Path(root_path).expanduser()
    result = ScanResult(root=str(root))
    if not root.is_dir():
        result.errors.append(f"not a directory: {root}")
        return result

    root_row = session.scalar(select(LibraryRoot).where(LibraryRoot.path == str(root)))
    if root_row is None:
        root_row = LibraryRoot(path=str(root))
        session.add(root_row)

    existing = {m.path: m for m in session.scalars(select(Media).where(Media.path.like(f"{root}%")))}
    seen: set[str] = set()
    games_by_key: dict[str, Game] = {}

    for path in iter_media(root):
        result.files_found += 1
        spath = str(path)
        seen.add(spath)
        fmt = EXTENSIONS[path.suffix.lower()]
        try:
            st = path.stat()
        except OSError as exc:
            result.errors.append(f"{spath}: {exc}")
            continue
        media = existing.get(spath)
        if media and media.mtime == st.st_mtime and media.size == st.st_size and not media.missing:
            continue
        parsed = parse_filename(path.name)
        info = inspect_file(path, fmt)
        family = "disk" if fmt in DISK_FORMATS else fmt
        group_key = f"{path.parent}|{parsed.normalized}|{family}"
        if media:
            media.size, media.mtime, media.missing, media.info = st.st_size, st.st_mtime, False, info
            result.media_updated += 1
            continue
        game = games_by_key.get(group_key) or session.scalar(select(Game).where(Game.group_key == group_key))
        if game is None:
            title = parsed.title
            if fmt == "sid" and info.get("title"):
                title = info["title"]
            game = Game(
                group_key=group_key, title=title, normalized_title=normalize_key(title),
                alternate_names=[parsed.title] if title != parsed.title else [],
                publisher=parsed.publisher or (info.get("author") if fmt == "sid" else None),
                year=parsed.year or _year_from(info.get("released")),
                category=CATEGORY_BY_FORMAT.get(fmt, "game"), format=fmt, tags=parsed.tags,
                source_root=str(root), num_disks=0,
                joystick_port=2 if fmt in DISK_FORMATS | {"prg", "crt", "t64"} else None,
                media=[],
            )
            session.add(game)
            result.games_created += 1
        games_by_key[group_key] = game
        game.media.append(Media(path=spath, storage="local", format=fmt, disk_number=parsed.disk_number,
                                label=parsed.disk_label, size=st.st_size, mtime=st.st_mtime, info=info))
        game.num_disks = max(len(game.media), parsed.disk_total or 0)
        result.media_added += 1
        if progress and result.files_found % 200 == 0:
            progress(result)

    for spath, media in existing.items():
        if spath not in seen and not media.missing:
            media.missing = True  # recorded only — nothing is deleted
            result.missing += 1

    root_row.last_scan = datetime.now(UTC)
    root_row.file_count = result.files_found
    root_row.last_error = "; ".join(result.errors[:5]) or None
    session.commit()
    return result


def _year_from(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"(19[78]\d|20[0-2]\d)", text)
    return int(m.group(1)) if m else None
