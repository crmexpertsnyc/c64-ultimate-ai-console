"""🔧 Repair & diagnostics assistant — "black screen", "garbled characters", "no sound"… → known causes, checks,
parts, manuals and repair videos.

* The knowledge base (``app/data/repair_kb.json``) holds the well-known C64 / C64C / SX-64 / 1541 faults and the
  C64 Ultimate's (which has no original chips — its problems are settings, cables, USB devices and firmware).
* With an AI model the description is read in context: the model ranks the knowledge base's causes (it can't
  invent new ones), adds follow-up questions and extra checks. Without one, keyword matching picks the symptom.
* The original power supply warning is always shown for original machines — it is the classic chip killer.
* Parts come from the 🛒 shop catalog by tag; videos from the monitored YouTube channels (Jan Beta, Adrian's
  Digital Basement…) plus a YouTube search link.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from sqlalchemy import select

from app.models.db import NewsItem

from .ai_json import ask_json, strs
from .ask import AskError

log = logging.getLogger("c64.repair")

KB_FILE = Path(__file__).resolve().parent.parent / "data" / "repair_kb.json"
RANK = {"high": 0, "medium": 1, "low": 2}
_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"no", "not", "the", "and", "on", "my", "it", "is", "a", "an", "of", "or", "to", "in", "with", "doesn", "don", "t"}


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


class RepairService:
    def __init__(self, container, kb_file: Path = KB_FILE):  # noqa: ANN001
        self.c = container
        self.kb_data = json.loads(kb_file.read_text(encoding="utf-8"))
        self.symptoms = {s["id"]: s for s in self.kb_data["symptoms"]}
        self.machines = {m["id"]: m for m in self.kb_data["machines"]}

    def kb(self) -> dict[str, Any]:
        return {"machines": self.kb_data["machines"],
                "symptoms": [{"id": s["id"], "title": s["title"], "machines": s["machines"]} for s in self.kb_data["symptoms"]],
                "references": self.kb_data.get("references", [])}

    def match(self, machine: str, text: str, limit: int = 2) -> list[str]:
        """Symptoms for this machine whose keywords appear in the description (phrases count more)."""
        low, words = text.lower(), _words(text)
        scored = []
        for s in self.kb_data["symptoms"]:
            if machine not in s["machines"]:
                continue
            score = 0.0
            for kw in s["keywords"]:
                if " " in kw and kw in low:
                    score += 3
                elif kw in words:
                    score += 1.5
            score += 0.5 * len((words & _words(s["title"])) - _STOP)
            if score:
                scored.append((score, s["id"]))
        return [sid for _, sid in sorted(scored, reverse=True)[:limit]]

    async def diagnose(self, machine: str, text: str, symptom_id: str | None = None) -> dict[str, Any]:
        if machine not in self.machines:
            raise ValueError(f"unknown machine {machine!r}")
        ids = [symptom_id] if symptom_id in self.symptoms else self.match(machine, text)
        if not ids and not text.strip():
            raise ValueError("describe the problem or pick a symptom")
        causes = [{**cause, "ref": f"{sid}#{n}", "symptom": self.symptoms[sid]["title"]}
                  for sid in ids for n, cause in enumerate(self.symptoms[sid]["causes"])]
        causes.sort(key=lambda x: RANK.get(x["likelihood"], 3))
        out: dict[str, Any] = {"machine": self.machines[machine], "symptoms": [self.symptoms[i]["title"] for i in ids],
                               "summary": None, "questions": [], "extraChecks": [], "ai": False, "aiError": None}
        if text.strip() and self.c.ask.provider.configured:
            try:
                causes, extra = await self._ai_rank(machine, text, ids, causes)
                out.update(extra, ai=True)
            except AskError as exc:
                out["aiError"] = str(exc)
        if not causes:
            out["summary"] = out["summary"] or ("I couldn't match that to a known fault for this machine — try picking "
                                                "the closest symptom from the list.")
        tags = [t for cz in causes[:4] for t in cz.get("tags", [])]
        safety = [s for s in self.kb_data["safety"] if machine in s["machines"]]
        tags += [t for s in safety for t in s["tags"]]
        out.update(causes=causes, safety=safety, parts=self._parts(tags), references=self._refs(machine),
                   videos=self._videos(ids, machine), services=self._services(tags))
        return out

    async def _ai_rank(self, machine: str, text: str, ids: list[str], causes: list[dict[str, Any]]):
        """The model ranks known causes (by ref) for this description — it can't add new ones."""
        pool = {f"{s['id']}#{n}": {**cz, "ref": f"{s['id']}#{n}", "symptom": s["title"]}
                for s in self.kb_data["symptoms"] if machine in s["machines"] for n, cz in enumerate(s["causes"])}
        lines = "\n".join(f"[{ref}] {c['symptom']} → {c['title']} ({c['likelihood']}): {c['why']}" for ref, c in pool.items())
        m = self.machines[machine]
        system = ("You are an experienced Commodore 64 repair technician helping a hobbyist. Use ONLY the known causes "
                  "listed (by their [ref]). Rank the ones that fit the description best. Be practical and safety-minded. "
                  "Reply with one JSON object: {\"summary\": str (2 sentences, plain words), \"ranked\": [{\"ref\": str, "
                  "\"why\": str (max 160 chars, why it fits THIS description)}] (2-6), \"questions\": [str] (0-3 short "
                  "follow-up questions that would narrow it down), \"checks\": [str] (0-3 extra quick checks)}.")
        user = f"Machine: {m['name']} — {m['note']}\nProblem: {text.strip()[:600]}\n\nKnown causes:\n{lines}"
        data, _ = await ask_json(self.c.ask, system, user, max_tokens=1800, what="diagnosis")
        ranked = []
        for r in data.get("ranked") or []:
            ref = str(r.get("ref") or "").strip("[] ") if isinstance(r, dict) else ""
            if ref in pool and all(x["ref"] != ref for x in ranked):
                ranked.append({**pool[ref], "aiWhy": strs([r.get("why")], 1, 200)[0] if r.get("why") else None})
        if not ranked:                              # unusable reply → keep the keyword match
            ranked = causes
        else:
            ranked += [cz for cz in causes if all(x["ref"] != cz["ref"] for x in ranked)][:3]
        extra = {"summary": strs([data.get("summary")], 1, 400)[0] if data.get("summary") else None,
                 "questions": strs(data.get("questions"), 3, 200), "extraChecks": strs(data.get("checks"), 3, 200)}
        return ranked, extra

    def _parts(self, tags: list[str]) -> list[dict[str, Any]]:
        shop = getattr(self.c, "shop", None)
        if shop is None or not tags:
            return []
        want = list(dict.fromkeys(t for t in tags if t))
        return shop.items(tags=want)[:8]

    def _services(self, tags: list[str]) -> list[dict[str, Any]]:
        shop = getattr(self.c, "shop", None)
        if shop is None:
            return []
        return [s for s in shop.catalog()["services"] if not s["tags"] or set(tags) & set(s["tags"]) or "repair" in s["tags"]]

    def _refs(self, machine: str) -> list[dict[str, Any]]:
        return [r for r in self.kb_data.get("references", []) if not r.get("machines") or machine in r["machines"]]

    def _videos(self, ids: list[str], machine: str) -> dict[str, Any]:
        query = next((self.kb_data["videoKeywords"].get(i) for i in ids if i in self.kb_data["videoKeywords"]),
                     "C64 Ultimate" if machine == "c64-ultimate" else "Commodore 64 repair")
        words = [w for w in _words(query) if w not in {"c64", "commodore", "64", "repair", "fix", "ultimate"}]
        found = []
        if words:
            with self.c.sf() as s:
                rows = s.scalars(select(NewsItem).where(NewsItem.kind == "video").order_by(NewsItem.published_at.desc())
                                 .limit(400)).all()
                for r in rows:
                    title = r.title.lower()
                    if any(w in title for w in words) and re.search(r"c64|commodore|1541|sid|vic", title):
                        found.append(self.c.news.to_dict(r))
        return {"items": found[:6], "searchUrl": f"https://www.youtube.com/results?search_query={quote_plus(query)}",
                "query": query}


def attach(container) -> RepairService:  # noqa: ANN001
    return RepairService(container)
