"""Command router: validated Intent → one approved service method.

This is the only place natural-language commands turn into device actions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.ai.engine import CommandEngine
from app.ai.intents import Intent, IntentType
from app.library.repository import LibraryRepository, game_to_dict
from app.models.db import Game
from app.ultimate.client import UltimateError
from app.ultimate.drives import drive_to_dict
from app.ultimate.input import InputUnsupported, LegacyStateError

from .assembly64 import Assembly64Error
from .audit import AuditService
from .catalog import CatalogService
from .device import DeviceService
from .launcher import Launcher, LaunchError

log = logging.getLogger("c64.commands")


@dataclass
class CommandResult:
    ok: bool
    message: str
    intent: dict[str, Any] | None = None
    data: Any = None
    needs_confirmation: bool = False
    candidates: list[dict[str, Any]] = field(default_factory=list)
    online_candidates: list[dict[str, Any]] = field(default_factory=list)
    interpretation: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "message": self.message, "intent": self.intent, "data": self.data,
                "needsConfirmation": self.needs_confirmation, "candidates": self.candidates,
                "onlineCandidates": self.online_candidates, "interpretation": self.interpretation}


class CommandRouter:
    def __init__(self, device: DeviceService, launcher: Launcher, engine: CommandEngine,
                 session_factory: sessionmaker, audit: AuditService, catalog: CatalogService | None = None):
        self.catalog = catalog
        self.device = device
        self.launcher = launcher
        self.engine = engine
        self.sf = session_factory
        self.audit = audit
        self.ask = None  # AskService, set by the container
        self.taste = None  # TasteService, set by the container (searches and questions teach recommendations)

    async def handle_text(self, text: str, *, source: str = "command", context: dict[str, Any] | None = None,
                          confirm: bool = False, use_ai: bool = True) -> CommandResult:
        context = dict(context or {})
        context.setdefault("currentGame", self.launcher.session.title)
        intent, meta = await self.engine.interpret(text, context, use_ai=use_ai)
        if self.taste is not None and intent.intent in (IntentType.PLAY_GAME, IntentType.SEARCH_GAME, IntentType.ASK):
            self.taste.record("ask" if intent.intent == IntentType.ASK else "search", text=text,
                              title=intent.game if intent.intent != IntentType.ASK else None)
        result = await self.execute(intent, source=source, user_command=text, context=context, confirm=confirm)
        result.interpretation = meta
        return result

    async def execute(self, intent: Intent, *, source: str = "api", user_command: str | None = None,
                      context: dict[str, Any] | None = None, confirm: bool = False) -> CommandResult:
        context = context or {}
        idict = intent.model_dump(mode="json", exclude_none=True)
        if intent.intent == IntentType.UNKNOWN:
            return CommandResult(False, "Sorry, I didn't understand that. Try “Play Bruce Lee”, “Put disk 2 in”, "
                                        "“Press fire”, “Open the menu” or “What's mounted?”.", idict)
        if intent.intent == IntentType.POWER_OFF and source == "mcp":
            return CommandResult(False, "Power off is not available to MCP clients; use the console UI.", idict)
        if intent.intent == IntentType.POWER_OFF and not confirm:
            return CommandResult(False, "Power off needs confirmation — this switches the C64 Ultimate off.",
                                 idict, needs_confirmation=True)
        if not self.device.connected and intent.intent not in (IntentType.SEARCH_GAME, IntentType.SHOW_DEVICE_INFO,
                                                                 IntentType.SHOW_CURRENT_GAME):
            return CommandResult(False, f"C64 Ultimate is not connected ({self.device.last_error or 'offline'}).", idict)
        try:
            async with self.audit.action(source, f"command.{intent.intent.value}", user_command, idict) as rec:
                result = await self._dispatch(intent, source, user_command, context)
                result.intent = idict
                rec.set_response({"ok": result.ok, "message": result.message})
                if not result.ok:
                    rec.success = False
                    rec.error = result.message
            return result
        except (InputUnsupported, LegacyStateError, LaunchError, ValueError) as exc:
            return CommandResult(False, str(exc), idict)
        except UltimateError as exc:
            await self.device.release_all_inputs(reason="command failure")
            return CommandResult(False, f"Device error: {exc}", idict)

    # ---------------------------------------------------------------- dispatch
    async def _dispatch(self, i: Intent, source: str, cmd: str | None, ctx: dict[str, Any]) -> CommandResult:
        dev = self.device
        t = i.intent
        if t == IntentType.ASK:
            return await self._ask(i)
        if t in (IntentType.PLAY_GAME, IntentType.PLAY_SID, IntentType.PLAY_MOD):
            return await self._play(i, source, cmd, ctx)
        if t == IntentType.SEARCH_GAME:
            with self.sf() as s:
                repo = LibraryRepository(s)
                games, total = repo.search(i.game or "", limit=20)
                if not games:
                    games = [g for g, _ in repo.find_best(i.game or "", limit=10)]
                    total = len(games)
                data = [game_to_dict(g, include_media=False) for g in games]
            if not data and self.catalog is not None and self.catalog.configured:
                query = await self._identify(i.game or "", "games") or (i.game or "")
                try:
                    found, _ = await self._lookup_online(query, "games", i.variant)
                except Assembly64Error:
                    found = []
                if found:
                    hint = f"I think you mean “{query}”. " if query.lower() != (i.game or "").lower() else ""
                    return CommandResult(True, f"{hint}Not in your library — on Assembly64:",
                                         online_candidates=[_online_dict(r) for r in found[:8]])
            return CommandResult(True, f"Found {total} match{'es' if total != 1 else ''} for “{i.game}”.", data=data)
        if t == IntentType.MOUNT_DISK:
            session = await self.launcher.mount_disk(i.disk or 1, source, cmd)
            return CommandResult(True, f"Disk {i.disk} of {session['title']} inserted in drive A.", data=session)
        if t == IntentType.NEXT_DISK:
            session = await self.launcher.next_disk(source, cmd)
            return CommandResult(True, f"Disk {session['currentDisk']} of {session['title']} inserted.", data=session)
        if t == IntentType.PREVIOUS_DISK:
            session = await self.launcher.previous_disk(source, cmd)
            return CommandResult(True, f"Disk {session['currentDisk']} of {session['title']} inserted.", data=session)
        if t == IntentType.RESET:
            await dev.release_all_inputs("reset")
            await dev.client.reset()
            dev.caps.record_use("machineReset", True)
            return CommandResult(True, "C64 reset.")
        if t == IntentType.REBOOT:
            await dev.release_all_inputs("reboot")
            await dev.client.reboot()
            dev.caps.record_use("machineReboot", True)
            return CommandResult(True, "Ultimate rebooted (cartridge re-initialised).")
        if t == IntentType.POWER_OFF:
            if not dev.settings.ALLOW_POWER_OFF:
                return CommandResult(False, "Power off is disabled (ALLOW_POWER_OFF=false).")
            await dev.release_all_inputs("power off")
            await dev.client.power_off()
            dev.connected = False
            dev.publish_status()
            return CommandResult(True, "Power off sent.")
        if t == IntentType.PAUSE:
            await dev.client.pause()
            dev.caps.record_use("machinePause", True)
            return CommandResult(True, "C64 paused.")
        if t == IntentType.RESUME:
            await dev.client.resume()
            dev.caps.record_use("machineResume", True)
            return CommandResult(True, "C64 resumed.")
        if t == IntentType.OPEN_MENU:
            r = await dev.menu.open()
            return CommandResult(True, "Ultimate menu opened." + (f" ({r['note']})" if r.get("note") else ""), data=r)
        if t == IntentType.CLOSE_MENU:
            r = await dev.menu.close()
            return CommandResult(True, "Ultimate menu closed." + (f" ({r['note']})" if r.get("note") else ""), data=r)
        if t == IntentType.READ_MENU:
            screen = await dev.menu.read()
            if screen is None:
                return CommandResult(True, "The Ultimate menu is not open.", data=None)
            sel = f" Selected: “{screen.selected_text}”." if screen.selected_text else ""
            return CommandResult(True, f"Menu: {screen.title}.{sel}", data=screen.to_dict(include_cells=False))
        if t == IntentType.MENU_NAVIGATE:
            r = await dev.menu.navigate(i.menu_action or "down")
            msg = f"Menu {i.menu_action}: " + (f"now on “{r['after']['selectedText']}”" if r.get("after", {}).get(
                "selectedText") else (r.get("note") or "done"))
            return CommandResult(r.get("changed", False) or bool(r.get("note")), msg, data=r)
        if t == IntentType.PRESS_KEY:
            key = i.key or "return"
            if i.transition == "press":
                await dev.inputs.press_key(key)
            elif i.transition == "release":
                await dev.inputs.release_key(key)
            else:
                await dev.inputs.tap_key(key)
            return CommandResult(True, f"{i.transition.capitalize()} {key.upper()}.")
        if t == IntentType.TYPE_TEXT:
            text = (i.text or "") + ("\r" if i.press_return else "")
            n = await dev.inputs.type_text(text)
            return CommandResult(True, f"Typed {n} character{'s' if n != 1 else ''}"
                                       f"{' and pressed RETURN' if i.press_return else ''}.")
        if t == IntentType.JOYSTICK_INPUT:
            port = i.port or dev.inputs.joystick_port
            inputs = i.joystick or ["fire"]
            if i.transition == "press":
                await dev.inputs.press_joystick(inputs, port)
            elif i.transition == "release":
                await dev.inputs.release_joystick(inputs, port)
            else:
                await dev.inputs.tap_joystick(inputs, port)
            return CommandResult(True, f"Joystick {port}: {i.transition} {' + '.join(inputs)}.")
        if t == IntentType.SET_JOYSTICK_PORT:
            dev.inputs.set_joystick_port(i.port or 2)
            dev.publish_status()
            return CommandResult(True, f"Joystick commands now go to port {i.port}.")
        if t == IntentType.RELEASE_ALL:
            sent = await dev.inputs.release_all()
            return CommandResult(True, "All inputs released." if sent else "Nothing held (legacy mode never holds).")
        if t == IntentType.SHOW_DEVICE_INFO:
            st = dev.status()
            info = st.get("info") or {}
            msg = (f"{info.get('product') or 'C64 Ultimate'} “{info.get('hostname') or st['host']}” — firmware "
                   f"{info.get('firmwareVersion') or '?'}, FPGA {info.get('fpgaVersion') or '?'}, core "
                   f"{info.get('coreVersion') or '?'}, REST API {st.get('apiVersion') or '?'}, input mode "
                   f"{st['inputMode']}." if st["connected"] else f"Not connected: {st.get('lastError')}")
            return CommandResult(st["connected"], msg, data=st)
        if t == IntentType.SHOW_CURRENT_GAME:
            sess = self.launcher.session.to_dict()
            if not sess["title"]:
                return CommandResult(True, "Nothing has been launched from the console yet.", data=sess)
            disk = f", disk {sess['currentDisk']} of {sess['diskCount']}" if sess["diskCount"] > 1 else ""
            return CommandResult(True, f"Current game: {sess['title']}{disk}.", data=sess)
        if t == IntentType.SHOW_DRIVE_STATUS:
            drives = await dev.refresh_drives()
            parts = [f"Drive {d.id.upper() if len(d.id) == 1 else d.id}: "
                     + (d.image_file if d.mounted else "empty") + ("" if d.enabled else " (off)")
                     for d in drives if len(d.id) == 1]
            return CommandResult(True, "; ".join(parts) or "No drives reported.",
                                 data=[drive_to_dict(d) for d in drives])
        return CommandResult(False, f"No handler for {t.value}")

    async def _play(self, i: Intent, source: str, cmd: str | None, ctx: dict[str, Any]) -> CommandResult:
        if i.target == "browser":
            return await self._play_browser(i, ctx)
        category = {IntentType.PLAY_SID: "music", IntentType.PLAY_MOD: "music"}.get(i.intent)
        with self.sf() as s:
            repo = LibraryRepository(s)
            game: Game | None = None
            if i.use_selected:
                sel = ctx.get("selectedGameId")
                game = repo.get(int(sel)) if sel else None
                if game is None:
                    return CommandResult(False, "No game is selected — open one in the library first.")
            else:
                matches = repo.find_best(i.game or "", category=category)
                if i.intent == IntentType.PLAY_SID:
                    matches = [(g, sc) for g, sc in matches if g.format == "sid"] or matches
                if i.intent == IntentType.PLAY_MOD:
                    matches = [(g, sc) for g, sc in matches if g.format == "mod"] or matches
                strong = bool(matches) and matches[0][1] >= 0.9
                if not strong and not i.use_selected and self.catalog is not None and self.catalog.configured:
                    online = await self._play_online(i, source, cmd, "music" if category else "games")
                    if online is not None:
                        return online
                if not matches and self.engine.provider.configured:
                    titles = list(s.scalars(select(Game.title).limit(3000)))
                    guess = await self.engine.suggest_title(i.game or "", titles)
                    if guess:
                        matches = repo.find_best(guess, category=category)
                if not matches:
                    hint = "" if (self.catalog and self.catalog.configured) else (
                        " Scan your game folders, or configure the Assembly64 catalog to fetch titles online.")
                    return CommandResult(False, f"“{i.game}” was not found in your library or online.{hint}")
                top, score = matches[0]
                close = [m for m in matches[1:] if score - m[1] < 0.04 and m[0].title.lower() != top.title.lower()]
                if score < 1.0 and close:
                    return CommandResult(False, f"Which one did you mean for “{i.game}”?",
                                         candidates=[game_to_dict(g, include_media=False) for g, _ in matches[:5]])
                game = top
            game_id, title = game.id, game.title
        job = await self.launcher.launch(game_id, source=source, user_command=cmd)
        verb = "Playing" if i.intent != IntentType.PLAY_GAME else "Launching"
        return CommandResult(True, f"{verb} {title} ({job.method.replace('_', ' ')}).", data={"job": job.to_dict()})

    async def _ask(self, i: Intent) -> CommandResult:
        """A question: answered by the AI (with web search when configured). No machine action."""
        from .ask import AskError
        if self.ask is None:
            return CommandResult(False, "Ask mode is not available.")
        try:
            result = await self.ask.ask(i.text or "")
        except AskError as exc:
            return CommandResult(False, str(exc))
        return CommandResult(True, result["answer"], data={"ask": result})

    async def _play_browser(self, i: Intent, ctx: dict[str, Any]) -> CommandResult:
        """"Play X in the browser": open the best browser-friendly version in the emulator on the user's
        device (the real C64 is not touched). One-file cartridge releases win over cracked disk versions;
        if the library only has a disk version, the catalog is asked for a cartridge release first."""
        from app.library.formats import browser_playable

        from .catalog import is_one_file_release

        if i.intent != IntentType.PLAY_GAME:
            return CommandResult(False, "Music plays on your C64 — the browser emulator is for games.")
        pick: tuple[int, str] | None = None
        pick_is_cart = False
        with self.sf() as s:
            repo = LibraryRepository(s)
            if i.use_selected:
                sel = ctx.get("selectedGameId")
                g = repo.get(int(sel)) if sel else None
                if g is None:
                    return CommandResult(False, "No game is selected — open one in the library first.")
                if not browser_playable(g.format, g.category):
                    return CommandResult(False, f"{g.title} ({g.format.upper()}) can't run in the browser emulator.")
                return _open_in_browser(g.id, g.title)
            matches = [(g, sc) for g, sc in repo.find_best(i.game or "")
                       if sc >= 0.9 and browser_playable(g.format, g.category)]
            if matches:
                matches.sort(key=lambda m: (0 if m[0].format.lower() == "crt" else 1, -m[1]))
                g = matches[0][0]
                pick, pick_is_cart = (g.id, g.title), g.format.lower() == "crt"
        if (pick is None or not pick_is_cart) and self.catalog is not None and self.catalog.configured:
            try:
                found = await self.catalog.find_for_play(i.game or "", "games", i.variant, target="browser")
            except Assembly64Error as exc:
                log.warning("catalog lookup failed: %s", exc)
                found = []
            from app.library.titles import title_key
            exact = [r for r in found if title_key(r["name"]) == title_key(i.game or "")]
            best = exact[0] if exact else None
            if best and (pick is None or is_one_file_release(best)):
                try:
                    game_id = await self.catalog.fetch(best["id"], best["category"], best)
                except Assembly64Error as exc:
                    if pick is None:
                        return CommandResult(False, f"Found “{best['name']}” online but could not fetch it: {exc}")
                else:
                    with self.sf() as s:
                        g = s.get(Game, game_id)
                        return _open_in_browser(game_id, g.title if g else best["name"])
            if pick is None and found:
                return CommandResult(False, f"“{i.game}” isn't in your library; similar titles on Assembly64:",
                                     online_candidates=[_online_dict(r) for r in found[:8]])
        if pick is None:
            return CommandResult(False, f"“{i.game}” was not found in your library or online.")
        return _open_in_browser(*pick)

    async def _lookup_online(self, query: str, kind: str, variant: str | None) -> tuple[list, list]:
        from app.library.titles import title_key

        from .catalog import split_author
        assert self.catalog is not None
        found = await self.catalog.find_for_play(query, kind, variant)
        wanted = title_key(split_author(query)[0])
        return found, [r for r in found if title_key(r["name"]) == wanted]

    async def _identify(self, description: str, kind: str) -> str | None:
        """Ask the LLM (if configured) which real title a description refers to."""
        if not self.engine.provider.configured:
            return None
        ident = await self.engine.identify_title(description, kind)
        if not ident:
            return None
        query = ident["title"]
        if kind == "music" and ident.get("author") and " by " not in query.lower():
            query += f" by {ident['author']}"
        return query

    async def _play_online(self, i: Intent, source: str, cmd: str | None, kind: str) -> CommandResult | None:
        """Look the title up in the Assembly64 catalog, fetch the best match and launch it.
        Descriptions ("the karate game with the yellow jumpsuit") are first turned into a real
        title by the LLM when one is configured. Returns None when nothing usable was found."""
        assert self.catalog is not None
        query = i.game or ""
        identified = None
        try:
            found, exact = await self._lookup_online(query, kind, i.variant)
            if not exact:
                guess = await self._identify(query, kind)
                if guess and guess.lower() != query.lower():
                    found2, exact2 = await self._lookup_online(guess, kind, i.variant)
                    if exact2 or (found2 and not found):
                        found, exact, query, identified = found2, exact2, guess, guess
        except Assembly64Error as exc:
            log.warning("catalog lookup failed: %s", exc)
            return None
        if not found:
            return None
        others = [_online_dict(r) for r in found[:8]]
        prefix = f"I think you mean “{identified}”. " if identified else ""
        # Music: several different tunes often share a title — ask instead of guessing.
        if kind == "music" and len({(r.get("group") or "").lower() for r in exact}) > 1:
            return CommandResult(False, f"{prefix}Several tunes are called “{query}”. Which one? "
                                        f"(or say “play {query} by <composer>”)", online_candidates=others)
        if not exact:
            return CommandResult(False, f"{prefix}“{query}” isn't in your library; similar titles on Assembly64:",
                                 online_candidates=others)
        pick = exact[0]
        try:
            game_id = await self.catalog.fetch(pick["id"], pick["category"], pick)
        except Assembly64Error as exc:
            return CommandResult(False, f"Found “{pick['name']}” on Assembly64 but could not fetch it: {exc}",
                                 online_candidates=others)
        job = await self.launcher.launch(game_id, source=source, user_command=cmd)
        by = f" by {pick['group'].replace('_', ' ')}" if pick.get("group") else ""
        return CommandResult(True, f"{prefix}Playing {pick['name']}{by} from Assembly64 ({pick['source']}, "
                                   f"{job.method.replace('_', ' ')}).", data={"job": job.to_dict(), "gameId": game_id},
                             online_candidates=[o for o in others if o["id"] != pick["id"]][:6])


def _online_dict(r: dict[str, Any]) -> dict[str, Any]:
    return {k: r.get(k) for k in ("id", "category", "name", "group", "year", "source", "kind")}


def _open_in_browser(game_id: int, title: str) -> CommandResult:
    """The web app navigates to the emulator for this result (data.navigate)."""
    return CommandResult(True, f"💻 Opening {title} in the browser emulator (your C64 isn't used).",
                         data={"navigate": f"/emulate/{game_id}", "gameId": game_id, "target": "browser"})
