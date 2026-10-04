"""💾 Backups of everything that is yours and can't be downloaded again.

* What: the library database (game list, ratings, profiles, achievements, collection, events you added…), Browser
  Play saves, imported games, screenshots, recordings, box art, recaps, problem reports and settings. Databases are
  copied with SQLite's online backup, so a backup taken while the console runs is consistent.
* Not included (they come back by themselves): the catalog download cache, product photos, magazine covers, logs.
  The magazine search index is big but rebuildable: it goes into the weekly "full" backup only.
* Where: ``BACKUP_DIR`` (default: Documents\\C64 Console Backups); the newest ``BACKUP_KEEP`` daily backups and the
  last 4 full ones are kept. Runs nightly from the 🔄 scheduler, or now from Settings.
* Note: settings.json holds your API keys — keep the backup folder private.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("c64.backup")

FOLDERS = ("savestates", "imports", "screenshots", "recordings", "art", "recaps", "issues")
FILES = ("settings.json", "recommendations.json")
DATABASES = ("c64console.db",)
FULL_ONLY = ("magazines.db",)
FULL_KEEP = 4


def default_dir() -> Path:
    return Path(os.path.expanduser("~")) / "Documents" / "C64 Console Backups"


class BackupService:
    def __init__(self, settings_provider):  # noqa: ANN001
        self._settings = settings_provider

    @property
    def data(self) -> Path:
        return Path(self._settings().data_path)

    @property
    def folder(self) -> Path:
        d = (self._settings().BACKUP_DIR or "").strip()
        return Path(d) if d else default_dir()

    def _db_copy(self, src: Path, dest: Path) -> None:
        """A consistent copy of a live SQLite database (WAL included)."""
        s, d = sqlite3.connect(f"file:{src}?mode=ro", uri=True), sqlite3.connect(dest)
        try:
            s.backup(d)
        finally:                                   # closed, not just committed: Windows locks open files
            d.close()
            s.close()

    def run(self, full: bool | None = None) -> dict[str, Any]:
        full = datetime.now().weekday() == 6 if full is None else full     # Sundays: also the magazine index
        if full is False and not self.backups(kind="full"):
            full = True                                                    # the first backup is a full one
        self.folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        name = f"c64-console-{stamp}{'-full' if full else ''}.zip"
        target = self.folder / name
        part = target.with_suffix(".part")
        files = 0
        with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for db in DATABASES + (FULL_ONLY if full else ()):
                src = self.data / db
                if src.exists():
                    copy = Path(tmp) / db
                    self._db_copy(src, copy)
                    z.write(copy, db)
                    files += 1
            for f in FILES:
                if (self.data / f).exists():
                    z.write(self.data / f, f)
                    files += 1
            for folder in FOLDERS:
                root = self.data / folder
                if not root.exists():
                    continue
                for p in root.rglob("*"):
                    if p.is_file():
                        z.write(p, p.relative_to(self.data).as_posix())
                        files += 1
            z.writestr("README.txt", "C64 Ultimate AI Console backup\n\nTo restore: stop the console, then unzip into "
                                     "backend/data (replacing the files there) and start it again.\n")
        part.replace(target)
        removed = self._prune()
        size = round(target.stat().st_size / 1_048_576, 1)
        log.info("backup %s: %d files, %.1f MB", name, files, size)
        return {"backup": name, "files": files, "sizeMB": size, "full": full, "removed": removed,
                "folder": str(self.folder), "summary": f"{name} ({size} MB, {files} files)"}

    def backups(self, kind: str | None = None) -> list[dict[str, Any]]:
        if not self.folder.exists():
            return []
        out = []
        for p in sorted(self.folder.glob("c64-console-*.zip"), reverse=True):
            is_full = p.stem.endswith("-full")
            if kind == "full" and not is_full:
                continue
            st = p.stat()
            out.append({"name": p.name, "sizeMB": round(st.st_size / 1_048_576, 1), "full": is_full,
                        "at": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="minutes")})
        return out

    def _prune(self) -> int:
        keep = max(1, int(self._settings().BACKUP_KEEP or 14))
        daily = [b for b in self.backups() if not b["full"]]
        full = [b for b in self.backups() if b["full"]]
        gone = daily[keep:] + full[FULL_KEEP:]
        for b in gone:
            (self.folder / b["name"]).unlink(missing_ok=True)
        return len(gone)

    def status(self) -> dict[str, Any]:
        items = self.backups()
        return {"folder": str(self.folder), "keep": int(self._settings().BACKUP_KEEP or 14), "backups": items,
                "totalMB": round(sum(b["sizeMB"] for b in items), 1)}
