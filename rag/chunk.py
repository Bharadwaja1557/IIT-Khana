"""Chunk the menu corpus for the naive RAG baseline.

One chunk per (hall, day, meal) slot -> 294 chunks. See DECISIONS.md D22 for
the alternatives considered (per-item, per-hall-day).

Fairness note, because this baseline exists to be beaten and a rigged strawman
would make the whole comparison worthless:

  * Chunks carry hall metadata (`hall_type`, `location`) as well as the menu.
    Without it D8 category 7 (policy) would be unanswerable by construction.
  * Item text is `item_raw`, verbatim upstream — so the "(Non-Veg)" markers
    upstream writes ARE visible to the baseline. Stripping them would cripple it.
  * Base menu and extras are labelled separately, matching the source site and
    the way D8's gold answers are written.

What chunks deliberately do NOT carry: the Phase 1 derived diet tags
(`veg`/`egg`/`nonveg`). Those are the structured path's enrichment. A naive RAG
baseline embeds the readable menu text, and handing it a derived column it would
not have in the wild would flatter it rather than test it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, asdict
from pathlib import Path

DB = Path("db/khana.db")

DAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday"]
MEAL_ORDER = ["Breakfast", "Lunch", "Dinner"]


@dataclass(frozen=True)
class Chunk:
    chunk_id: str          # "hall-12__wednesday__dinner" — stable, human-readable
    hall: str
    day: str
    meal: str
    text: str              # what actually gets embedded
    n_items: int

    def as_dict(self) -> dict:
        return asdict(self)


def _slug(s: str) -> str:
    return s.lower().replace(" ", "-")


def build_chunks(db_path: Path = DB) -> list[Chunk]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    halls = {r["id"]: r for r in conn.execute("SELECT * FROM hall")}

    rows = conn.execute("""
        SELECT hall_id, day_of_week, meal, item_raw, is_extra, position
          FROM menu_item
         ORDER BY hall_id, day_of_week, meal, is_extra, position
    """).fetchall()

    grouped: dict[tuple, dict[int, list[str]]] = {}
    for r in rows:
        key = (r["hall_id"], r["day_of_week"], r["meal"])
        grouped.setdefault(key, {0: [], 1: []})[r["is_extra"]].append(r["item_raw"])

    chunks: list[Chunk] = []
    for (hall_id, day, meal), fields in grouped.items():
        h = halls[hall_id]
        base, extra = fields[0], fields[1]

        # Readable header. Hall type and location are included so the policy
        # category is answerable at all — see the module docstring.
        bits = [h["name"]]
        meta = ", ".join(x for x in (
            f"{h['hall_type']} hall" if h["hall_type"] else None,
            h["location"] or None,
        ) if x)
        header = f"{bits[0]} ({meta})" if meta else bits[0]

        lines = [f"{header} — {day} {meal}"]
        if base:
            lines.append(f"Menu: {', '.join(base)}")
        if extra:
            lines.append(f"Extras: {', '.join(extra)}")
        if not base and not extra:
            lines.append("Menu: (no items listed)")

        chunks.append(Chunk(
            chunk_id=f"{_slug(h['name'])}__{day.lower()}__{meal.lower()}",
            hall=h["name"], day=day, meal=meal,
            text="\n".join(lines),
            n_items=len(base) + len(extra),
        ))

    chunks.sort(key=lambda c: (c.hall, DAY_ORDER.index(c.day),
                              MEAL_ORDER.index(c.meal)))
    return chunks


if __name__ == "__main__":
    cs = build_chunks()
    print(f"{len(cs)} chunks\n")
    for c in cs[:3]:
        print(f"--- {c.chunk_id}  ({c.n_items} items) ---\n{c.text}\n")
