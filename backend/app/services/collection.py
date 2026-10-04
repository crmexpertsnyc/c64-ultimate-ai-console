"""📦 Collection manager — the physical side of the hobby: boxed games, cartridges, disks, tapes, hardware.

* Log what you own (or want): condition, boxed / complete, where it's stored, what you paid.
* Box art and titles come from your library when a game matches (the same title matching the library uses).
* 💲 Value guide: the median of current eBay asking prices (eBay's sold-price data needs special API access, so a
  "sold listings ↗" link is offered to check real sales); or type your own value.
* ⭐ Wishlist with a target price → a 💰 price watch in the shop: a listing at or under it shows up in What's new.
* Export: CSV, and a printable report for insurance (totals, conditions, values, dates).
"""

from __future__ import annotations

import csv
import html
import io
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.library.titles import title_key
from app.models.collection import CollectionItem
from app.models.db import Game

KINDS = {"game": "🎮 Game", "cartridge": "🧩 Cartridge", "disk": "💾 Disk", "tape": "📼 Tape", "hardware": "🖥 Hardware",
         "accessory": "🕹 Accessory", "book": "📚 Book / magazine", "other": "📦 Other"}
CONDITIONS = {"sealed": "Sealed", "mint": "Mint", "very-good": "Very good", "good": "Good", "fair": "Fair",
              "poor": "Poor", "parts": "For parts / not working"}
FIELDS = ("kind", "title", "platform", "edition", "condition", "boxed", "complete", "quantity", "serial", "notes",
          "location", "purchase_price", "purchase_date", "value", "currency", "game_id", "wishlist", "target_price")


class CollectionService:
    def __init__(self, container):  # noqa: ANN001
        self.c = container

    # ------------------------------------------------------------ items
    def to_dict(self, it: CollectionItem, cover: str | None = None) -> dict[str, Any]:
        return {"id": it.id, "kind": it.kind, "title": it.title, "platform": it.platform, "edition": it.edition,
                "condition": it.condition, "boxed": it.boxed, "complete": it.complete, "quantity": it.quantity,
                "serial": it.serial, "notes": it.notes, "location": it.location, "purchasePrice": it.purchase_price,
                "purchaseDate": it.purchase_date, "value": it.value, "currency": it.currency,
                "valueSource": it.value_source, "valueAt": it.value_at.isoformat() if it.value_at else None,
                "gameId": it.game_id, "coverUrl": cover, "wishlist": it.wishlist, "targetPrice": it.target_price,
                "createdAt": it.created_at.isoformat() if it.created_at else None}

    def list(self, kind: str | None = None, q: str | None = None, wishlist: bool | None = None) -> dict[str, Any]:
        with self.c.sf() as s:
            stmt = select(CollectionItem)
            if kind:
                stmt = stmt.where(CollectionItem.kind == kind)
            if wishlist is not None:
                stmt = stmt.where(CollectionItem.wishlist.is_(wishlist))
            if q:
                like = f"%{q.strip()[:60]}%"
                stmt = stmt.where(CollectionItem.title.ilike(like) | CollectionItem.notes.ilike(like)
                                  | CollectionItem.edition.ilike(like))
            rows = s.scalars(stmt.order_by(CollectionItem.kind, CollectionItem.title)).all()
            ids = {r.game_id for r in rows if r.game_id}
            covers = {g.id: g.cover_url for g in s.scalars(select(Game).where(Game.id.in_(ids)))}
            items = [self.to_dict(r, covers.get(r.game_id)) for r in rows]
            owned = s.scalars(select(CollectionItem).where(CollectionItem.wishlist.is_(False))).all()
        totals: dict[str, float] = {}
        for r in owned:
            if r.value:
                totals[r.currency] = round(totals.get(r.currency, 0) + r.value * (r.quantity or 1), 2)
        return {"items": items, "kinds": KINDS, "conditions": CONDITIONS,
                "summary": {"owned": sum(r.quantity or 1 for r in owned), "valued": sum(1 for r in owned if r.value),
                            "totals": totals}}

    def _match_game(self, s, title: str) -> int | None:  # noqa: ANN001
        key = title_key(title)
        g = s.scalars(select(Game).where(Game.normalized_title == key)).first() if key else None
        return g.id if g else None

    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        from app.profiles import profile_id
        with self.c.sf() as s:
            it = CollectionItem(profile_id=profile_id())
            self._apply(it, data)
            if not it.title.strip():
                raise ValueError("a title is needed")
            if it.game_id is None and it.kind in ("game", "cartridge", "disk", "tape"):
                it.game_id = self._match_game(s, it.title)
            s.add(it)
            s.commit()
            item_id = it.id
        self._sync_watch(item_id)
        return self.get(item_id)

    def from_library(self, game_id: int, data: dict[str, Any]) -> dict[str, Any]:
        with self.c.sf() as s:
            g = s.get(Game, game_id)
            if g is None:
                raise LookupError("no such game")
            title = g.title
        return self.create({"kind": "game", **data, "title": data.get("title") or title, "game_id": game_id})

    def get(self, item_id: int) -> dict[str, Any]:
        with self.c.sf() as s:
            it = s.get(CollectionItem, item_id)
            if it is None:
                raise LookupError("no such item")
            g = s.get(Game, it.game_id) if it.game_id else None
            return self.to_dict(it, g.cover_url if g else None)

    def update(self, item_id: int, data: dict[str, Any]) -> dict[str, Any]:
        with self.c.sf() as s:
            it = s.get(CollectionItem, item_id)
            if it is None:
                raise LookupError("no such item")
            if "value" in data and data["value"] != it.value:
                it.value_source, it.value_at = "your estimate", datetime.now(UTC)
            self._apply(it, data)
            it.updated_at = datetime.now(UTC)
            s.commit()
        self._sync_watch(item_id)
        return self.get(item_id)

    def delete(self, item_id: int) -> None:
        with self.c.sf() as s:
            it = s.get(CollectionItem, item_id)
            if it is None:
                raise LookupError("no such item")
            s.delete(it)
            s.commit()
        if getattr(self.c, "shop", None):
            self.c.shop.remove_watch(collection_item_id=item_id)

    @staticmethod
    def _apply(it: CollectionItem, data: dict[str, Any]) -> None:
        for f in FIELDS:
            if f not in data:
                continue
            v = data[f]
            if f == "kind" and v not in KINDS:
                raise ValueError(f"unknown kind {v!r}")
            if f == "condition" and v not in CONDITIONS and v is not None:
                raise ValueError(f"unknown condition {v!r}")
            if f == "currency":
                v = (str(v or "USD")[:3]).upper()
            if f == "title":
                v = str(v or "").strip()[:200]
            setattr(it, f, v)
        if it.kind is None:
            it.kind = "game"

    # ------------------------------------------------------------ value + wishlist
    def search_query(self, it: dict[str, Any]) -> str:
        words = [it["title"]]
        if it["kind"] in ("game", "cartridge", "disk", "tape"):
            words.append("C64" if it["platform"] in (None, "", "C64") else it["platform"])
            if it["kind"] in ("disk", "tape", "cartridge"):
                words.append(it["kind"])
            if it.get("boxed"):
                words.append("boxed")
        elif it["kind"] == "hardware" and "commodore" not in it["title"].lower():
            words.insert(0, "Commodore")
        return " ".join(words)[:100]

    async def estimate(self, item_id: int) -> dict[str, Any]:
        it = self.get(item_id)
        result = await self.c.shop.estimate(self.search_query(it))
        if result["value"] is not None:
            with self.c.sf() as s:
                row = s.get(CollectionItem, item_id)
                row.value, row.currency = result["value"], result["currency"] or row.currency
                row.value_source, row.value_at = result["basis"], datetime.now(UTC)
                s.commit()
        return {**result, "item": self.get(item_id)}

    def _sync_watch(self, item_id: int) -> None:
        shop = getattr(self.c, "shop", None)
        if shop is None:
            return
        it = self.get(item_id)
        if it["wishlist"] and it["targetPrice"]:
            shop.add_watch(self.search_query(it), it["targetPrice"], collection_item_id=item_id)
        else:
            shop.remove_watch(collection_item_id=item_id)

    # ------------------------------------------------------------ export
    def export_csv(self) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Kind", "Title", "Platform", "Edition", "Condition", "Boxed", "Complete", "Quantity", "Serial",
                    "Location", "Purchase price", "Purchase date", "Value (each)", "Currency", "Value source",
                    "Valued on", "Notes", "Wishlist"])
        for it in self.list()["items"]:
            w.writerow([KINDS.get(it["kind"], it["kind"]).split(" ", 1)[-1], it["title"], it["platform"], it["edition"] or "",
                        CONDITIONS.get(it["condition"] or "", ""), "yes" if it["boxed"] else "no",
                        "yes" if it["complete"] else "no", it["quantity"], it["serial"] or "", it["location"] or "",
                        it["purchasePrice"] or "", it["purchaseDate"] or "", it["value"] or "", it["currency"],
                        it["valueSource"] or "", (it["valueAt"] or "")[:10], it["notes"] or "", "yes" if it["wishlist"] else ""])
        return buf.getvalue()

    @staticmethod
    def _money(i: dict[str, Any]) -> str:
        if i["value"] is None:
            return ""
        return f"{i['value'] * (i['quantity'] or 1):,.2f} {html.escape(i['currency'])}"

    def report_html(self) -> str:
        """A printable inventory for insurance (owned items only)."""
        data = self.list(wishlist=False)
        e = html.escape
        rows = "".join(
            f"<tr><td>{e(KINDS.get(i['kind'], i['kind']).split(' ', 1)[-1])}</td><td>{e(i['title'])}"
            f"{'<br><small>' + e(i['edition']) + '</small>' if i['edition'] else ''}</td>"
            f"<td>{e(CONDITIONS.get(i['condition'] or '', '—'))}{' · boxed' if i['boxed'] else ''}"
            f"{' · complete' if i['complete'] else ''}</td>"
            f"<td>{i['quantity']}</td><td>{e(i['serial'] or '')}</td>"
            f"<td class=n>{self._money(i)}</td>"
            f"<td><small>{e(i['valueSource'] or '')}{' · ' + e(i['valueAt'][:10]) if i['valueAt'] else ''}</small></td></tr>"
            for i in data["items"])
        totals = " + ".join(f"{v:,.2f} {e(k)}" for k, v in data["summary"]["totals"].items()) or "—"
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        return f"""<!doctype html><html><head><meta charset="utf-8"><title>C64 collection inventory {today}</title>
<style>body{{font:14px system-ui,sans-serif;margin:24px;color:#111}}h1{{font-size:20px}}
table{{border-collapse:collapse;width:100%}}
td,th{{border-bottom:1px solid #ccc;padding:6px;text-align:left;vertical-align:top}}.n{{text-align:right;white-space:nowrap}}
small{{color:#555}}@media print{{button{{display:none}}}}</style></head><body>
<h1>Commodore collection — inventory</h1>
<p>Prepared {today} · {data['summary']['owned']} items · estimated total value <b>{totals}</b></p>
<p><small>Values are estimates (your own, or the median of current eBay asking prices on the date shown)
— not appraisals.</small></p>
<button onclick="print()">Print / save as PDF</button>
<table><thead><tr><th>Kind</th><th>Item</th><th>Condition</th><th>Qty</th><th>Serial</th><th class=n>Value</th>
<th>Basis</th></tr></thead>
<tbody>{rows}</tbody></table></body></html>"""


def attach(container) -> CollectionService:  # noqa: ANN001
    return CollectionService(container)
