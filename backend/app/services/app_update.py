"""⬆ Update channel: is a newer version of the console available?

* Release installs: the latest GitHub release of UPDATE_REPO ("owner/name") compared with this version.
* Git checkouts: `git fetch`, then how many commits the branch is behind its upstream.
Checked daily by the scheduler (and on demand). "Update now" (Windows, from the console's own computer or an admin
session) starts scripts/update.ps1 in the background; it pulls the new version, installs it and restarts the console.
Nothing is downloaded or changed by the check itself.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[3]          # project root (backend/app/services → ../../..)


def _version_tuple(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:3]) or (0,)


class AppUpdate:
    def __init__(self, settings_provider, version: str):  # noqa: ANN001
        self._settings = settings_provider
        self.version = version
        self.state: dict[str, Any] = {"checkedAt": None, "newer": False, "latest": None, "url": None, "notes": None,
                                      "source": None, "error": None, "behind": None}
        self.http: httpx.AsyncClient | None = None    # tests inject a mock

    @property
    def repo(self) -> str:
        r = (self._settings().UPDATE_REPO or "").strip()
        return r if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", r) else ""

    @property
    def is_git(self) -> bool:
        return (ROOT / ".git").exists()

    def can_apply(self) -> bool:
        return sys.platform == "win32" and (ROOT / "scripts" / "update.ps1").is_file()

    def status(self) -> dict[str, Any]:
        how = (r"powershell -ExecutionPolicy Bypass -File scripts\update.ps1" if sys.platform == "win32"
               else "bash scripts/update.sh")
        return {"version": self.version, **self.state, "repo": self.repo, "git": self.is_git,
                "canApply": self.can_apply(), "command": how}

    async def _git(self, *args: str) -> str:
        proc = await asyncio.create_subprocess_exec("git", *args, cwd=str(ROOT), stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), 60)
        if proc.returncode != 0:
            raise RuntimeError((err or out).decode("utf-8", "replace").strip()[:200] or "git failed")
        return out.decode("utf-8", "replace").strip()

    async def check(self) -> dict[str, Any]:
        st: dict[str, Any] = {"checkedAt": datetime.now(UTC).isoformat(), "newer": False, "latest": None, "url": None,
                              "notes": None, "source": None, "error": None, "behind": None}
        try:
            if self.repo:
                st["source"] = "release"
                client = self.http or httpx.AsyncClient(timeout=20, headers={"User-Agent": "c64-console-update-check"})
                try:
                    r = await client.get(f"https://api.github.com/repos/{self.repo}/releases/latest")
                    if r.status_code == 404:
                        raise RuntimeError(f"{self.repo} has no releases yet")
                    r.raise_for_status()
                    rel = r.json()
                finally:
                    if self.http is None:
                        await client.aclose()
                st["latest"] = rel.get("tag_name")
                st["url"] = rel.get("html_url")
                st["notes"] = (rel.get("body") or "")[:2000] or None
                st["newer"] = _version_tuple(st["latest"]) > _version_tuple(self.version)
            elif self.is_git:
                st["source"] = "git"
                try:
                    await self._git("rev-parse", "--abbrev-ref", "@{u}")
                except RuntimeError as exc:
                    raise RuntimeError("this copy has no upstream branch to update from — set UPDATE_REPO "
                                       "(owner/name on GitHub) in Settings") from exc
                await self._git("fetch", "--quiet")
                behind = int(await self._git("rev-list", "--count", "HEAD..@{u}") or 0)
                st["behind"] = behind
                st["newer"] = behind > 0
                st["latest"] = f"{behind} new change{'s' if behind != 1 else ''}" if behind else "up to date"
            else:
                st["error"] = "no update source — set UPDATE_REPO (owner/name on GitHub) in Settings"
        except (TimeoutError, httpx.HTTPError, RuntimeError, ValueError, OSError) as exc:
            st["error"] = str(exc)[:200] or type(exc).__name__
        self.state = st
        return self.status()

    def apply(self) -> dict[str, Any]:
        """Start scripts/update.ps1 detached; it restarts the console when done (Windows installs only)."""
        if not self.can_apply():
            raise RuntimeError(f"Update from a terminal: {self.status()['command']}")
        script = ROOT / "scripts" / "update.ps1"
        flags = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        env = {**os.environ, **({"C64_CONSOLE_REPO": self.repo} if self.repo else {})}
        subprocess.Popen(["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",  # noqa: S603, S607
                          "-File", str(script)], cwd=str(ROOT), env=env, creationflags=flags, close_fds=True)
        return {"started": True, "log": "backend/data/logs/update.log"}
