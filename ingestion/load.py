"""Build canonical rows from cached upstream JSON and load them into SQLite."""

from __future__ import annotations

import collections
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import source
from .parse import DAYS, MEALS, build_clusters, normalize, split_items

SCHEMA = Path(__file__).resolve().parent.parent / "db" / "schema.sql"


@dataclass(frozen=True)
class Item:
    hall_id: int
    day_of_week: str
    meal: str
    item_raw: str
    item_normalized: str
    is_extra: int
    position: int
    source_row_id: int | None
    last_updated: str | None


@dataclass
class SlotStatus:
    hall_id: int
    day_of_week: str
    meal: str
    n_base: int
    n_extra: int
    status: str
    note: str | None = None


def build_rows(halls: list[dict], weekly: dict[int, list[dict]]):
    """-> (items, slots). Pure: no database involved, so it is unit-testable."""
    items: list[Item] = []
    slots: list[SlotStatus] = []
    seen: set[tuple[int, str, str]] = set()

    for hall_id, rows in sorted(weekly.items()):
        for row in rows:
            day, meal = row.get("dayOfWeek"), row.get("mealType")
            key = (hall_id, day, meal)

            if day not in DAYS or meal not in MEALS:
                slots.append(SlotStatus(hall_id, str(day), str(meal), 0, 0,
                                        "malformed", f"unknown day/meal {day!r}/{meal!r}"))
                continue
            if key in seen:
                slots.append(SlotStatus(hall_id, day, meal, 0, 0,
                                        "malformed", "duplicate slot in source"))
                continue
            seen.add(key)

            counts = {}
            for is_extra, field in ((0, "description"), (1, "extras")):
                parsed = split_items(row.get(field))
                kept = 0
                for raw in parsed:
                    norm = normalize(raw)
                    if not norm:
                        continue  # e.g. a stray "-" or "."
                    items.append(Item(hall_id, day, meal, raw, norm, is_extra,
                                      kept, row.get("id"), row.get("lastUpdated")))
                    kept += 1
                counts[is_extra] = kept

            n_base, n_extra = counts[0], counts[1]
            if n_base == 0 and n_extra == 0:
                status, note = "empty", "no items parsed from either field"
            elif n_base == 0:
                status, note = "empty", "description empty; extras only"
            else:
                status, note = "present", None
            slots.append(SlotStatus(hall_id, day, meal, n_base, n_extra, status, note))

    # Missing slots: a hall x day x meal the source never shipped at all.
    for hall_id in weekly:
        for day in DAYS:
            for meal in MEALS:
                if (hall_id, day, meal) not in seen:
                    slots.append(SlotStatus(hall_id, day, meal, 0, 0,
                                            "malformed", "slot absent from source"))
    return items, slots


def connect(db_path: str | Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA.read_text())
    return conn


def ingest(conn: sqlite3.Connection, halls: list[dict], weekly: dict[int, list[dict]],
           cluster_threshold: float = 85.0):
    """Load canonical rows. Idempotent: a rebuild inside one transaction."""
    items, slots = build_rows(halls, weekly)

    counts = collections.Counter(i.item_normalized for i in items)
    canon = build_clusters(counts, threshold=cluster_threshold)

    with conn:  # single transaction; a failure leaves the previous DB intact
        conn.execute("DELETE FROM menu_item")
        conn.execute("DELETE FROM slot")
        conn.execute("DELETE FROM hall")

        conn.executemany(
            "INSERT INTO hall (id, name, hall_type, location) VALUES (?,?,?,?)",
            [(h["id"], h["name"], h.get("type"), (h.get("location") or "").strip())
             for h in halls],
        )
        conn.executemany(
            """INSERT INTO menu_item
                 (hall_id, day_of_week, meal, item_raw, item_normalized,
                  item_canonical, tags, is_extra, position, source_row_id, last_updated)
               VALUES (?,?,?,?,?,?,NULL,?,?,?,?)""",
            [(i.hall_id, i.day_of_week, i.meal, i.item_raw, i.item_normalized,
              canon.get(i.item_normalized, i.item_normalized),
              i.is_extra, i.position, i.source_row_id, i.last_updated) for i in items],
        )
        conn.executemany(
            """INSERT INTO slot (hall_id, day_of_week, meal, n_base, n_extra, status, note)
               VALUES (?,?,?,?,?,?,?)""",
            [(s.hall_id, s.day_of_week, s.meal, s.n_base, s.n_extra, s.status, s.note)
             for s in slots],
        )
    return items, slots, canon


def coverage_report(conn: sqlite3.Connection) -> str:
    """Printed on every ingest. Missing data silently becomes wrong answers."""
    cur = conn.cursor()
    n_halls = cur.execute("SELECT COUNT(*) FROM hall").fetchone()[0]
    expected = n_halls * len(DAYS) * len(MEALS)

    by_status = dict(cur.execute(
        "SELECT status, COUNT(*) FROM slot GROUP BY status").fetchall())
    present = by_status.get("present", 0)
    empty = by_status.get("empty", 0)
    malformed = by_status.get("malformed", 0)
    total = present + empty + malformed

    n_items = cur.execute("SELECT COUNT(*) FROM menu_item").fetchone()[0]
    n_norm = cur.execute("SELECT COUNT(DISTINCT item_normalized) FROM menu_item").fetchone()[0]
    n_canon = cur.execute("SELECT COUNT(DISTINCT item_canonical) FROM menu_item").fetchone()[0]
    n_extra = cur.execute("SELECT COUNT(*) FROM menu_item WHERE is_extra=1").fetchone()[0]
    untagged = cur.execute("SELECT COUNT(*) FROM menu_item WHERE tags IS NULL").fetchone()[0]

    lines = [
        "",
        "=" * 62,
        "COVERAGE REPORT",
        "=" * 62,
        f"  halls                {n_halls}",
        f"  slots expected       {expected}   ({n_halls} halls x 7 days x 3 meals)",
        f"  slots accounted for  {total}",
        "",
        f"    present            {present:>4}  ({100*present/expected:.1f}%)",
        f"    empty              {empty:>4}",
        f"    malformed          {malformed:>4}",
        "",
        f"  items                {n_items}",
        f"    base menu          {n_items - n_extra}",
        f"    extras             {n_extra}",
        f"  distinct normalized  {n_norm}",
        f"  distinct canonical   {n_canon}   (clustering merged {n_norm - n_canon})",
        f"  untagged items       {untagged}   (tagger lands in Phase 1 C2)",
    ]

    if total != expected:
        lines += ["", f"  !! slot accounting mismatch: {total} != {expected}"]

    problems = cur.execute(
        """SELECT h.name, s.day_of_week, s.meal, s.status, s.note
             FROM slot s JOIN hall h ON h.id = s.hall_id
            WHERE s.status != 'present' ORDER BY h.name, s.day_of_week""").fetchall()
    if problems:
        lines += ["", "  non-present slots:"]
        lines += [f"    {n:<9} {d:<10} {m:<10} {st:<10} {note or ''}"
                  for n, d, m, st, note in problems]

    lines.append("=" * 62)
    return "\n".join(lines)
