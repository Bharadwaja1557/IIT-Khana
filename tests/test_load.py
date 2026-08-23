"""Ingestion tests: canonical row shape, coverage accounting, idempotency.

Uses the same verbatim upstream fixtures as test_parse.py.
"""

from __future__ import annotations

import collections
import json
from pathlib import Path

import pytest

from ingestion.load import build_rows, connect, coverage_report, ingest

FIX = Path(__file__).parent / "fixtures"
HALLS = json.loads((FIX / "halls.json").read_text())
ROWS = json.loads((FIX / "menu_rows.json").read_text())


@pytest.fixture
def weekly() -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = collections.defaultdict(list)
    for r in ROWS:
        out[r["hallId"]].append(r)
    return dict(out)


@pytest.fixture
def db(tmp_path, weekly):
    conn = connect(tmp_path / "test.db")
    ingest(conn, HALLS, weekly)
    yield conn
    conn.close()


# ------------------------------------------------------------ canonical rows


def test_canonical_row_shape(db):
    cols = {r[1] for r in db.execute("PRAGMA table_info(menu_item)")}
    for required in ("hall_id", "day_of_week", "meal", "item_raw",
                     "item_normalized", "tags"):
        assert required in cols


def test_no_date_or_serving_window_columns(db):
    """D3 and D11: neither exists in the source, so neither exists here."""
    cols = {r[1] for r in db.execute("PRAGMA table_info(menu_item)")}
    assert "date" not in cols
    assert "serving_window" not in cols


def test_every_item_is_tagged(db):
    """Phase 1 C2: tags are populated at ingest from item_raw."""
    n = db.execute("SELECT COUNT(*) FROM menu_item WHERE tags IS NULL").fetchone()[0]
    assert n == 0
    bad = db.execute(
        "SELECT DISTINCT tags FROM menu_item "
        "WHERE tags NOT IN ('veg','egg','nonveg','unclear')").fetchall()
    assert bad == []


def test_tags_are_derived_from_raw_not_normalized(db):
    """The (Non-Veg) marker lives in a parenthetical that normalization strips."""
    row = db.execute(
        "SELECT tags FROM menu_item WHERE item_raw = 'Chicken Lolipop (Non-Veg)'"
    ).fetchone()
    assert row is not None and row[0] == "nonveg"


def test_items_carry_provenance(db):
    row = db.execute(
        "SELECT source_row_id, last_updated FROM menu_item LIMIT 1").fetchone()
    assert row[0] is not None and row[1] is not None


def test_extras_are_flagged_separately(db):
    """description = base menu, extras = paid add-ons. The distinction matters:
    non-veg lives almost entirely in extras."""
    n_extra = db.execute("SELECT COUNT(*) FROM menu_item WHERE is_extra=1").fetchone()[0]
    assert n_extra > 0
    raws = {r[0] for r in db.execute(
        "SELECT item_raw FROM menu_item WHERE is_extra=1")}
    assert "Chicken Lolipop (Non-Veg)" in raws


# ------------------------------------------------------------------ coverage


def test_coverage_accounts_for_every_expected_slot(weekly):
    """Every hall x day x meal must be classified, including ones the source
    never shipped. Silent gaps become wrong answers later."""
    items, slots = build_rows(HALLS, weekly)
    assert len(slots) == len(weekly) * 7 * 3
    keys = {(s.hall_id, s.day_of_week, s.meal) for s in slots}
    assert len(keys) == len(slots), "a slot was classified twice"


def test_absent_slots_are_marked_malformed_not_dropped(weekly):
    items, slots = build_rows(HALLS, weekly)
    absent = [s for s in slots if s.note == "slot absent from source"]
    # The fixture ships only 8 of 6*21 slots, so most are absent.
    assert len(absent) == len(weekly) * 21 - len(ROWS)
    assert all(s.status == "malformed" for s in absent)


def test_coverage_report_mentions_the_gap(db):
    report = coverage_report(db)
    assert "slots expected" in report
    assert "malformed" in report
    assert "slot absent from source" in report


def test_empty_slot_detected(tmp_path):
    weekly = {1: [{"hallId": 1, "dayOfWeek": "Monday", "mealType": "Lunch",
                   "description": "", "extras": None, "id": 1, "lastUpdated": "x"}]}
    items, slots = build_rows([HALLS[0]], weekly)
    monday = next(s for s in slots if (s.day_of_week, s.meal) == ("Monday", "Lunch"))
    assert monday.status == "empty"
    assert not [i for i in items if i.day_of_week == "Monday"]


def test_unknown_day_is_malformed_not_crash():
    weekly = {1: [{"hallId": 1, "dayOfWeek": "Funday", "mealType": "Lunch",
                   "description": "Dal", "extras": None, "id": 1, "lastUpdated": "x"}]}
    items, slots = build_rows([HALLS[0]], weekly)
    bad = [s for s in slots if s.status == "malformed" and "Funday" in (s.note or "")]
    assert len(bad) == 1
    assert not any(i.day_of_week == "Funday" for i in items)


# --------------------------------------------------------------- idempotency


def _fingerprint(conn):
    rows = conn.execute(
        """SELECT hall_id, day_of_week, meal, item_raw, item_normalized,
                  item_canonical, is_extra, position
             FROM menu_item
            ORDER BY hall_id, day_of_week, meal, is_extra, position""").fetchall()
    return len(rows), rows


def test_reingest_does_not_duplicate(tmp_path, weekly):
    conn = connect(tmp_path / "idem.db")
    ingest(conn, HALLS, weekly)
    first = _fingerprint(conn)
    for _ in range(3):
        ingest(conn, HALLS, weekly)
    assert _fingerprint(conn) == first
    conn.close()


def test_natural_key_is_unique(db):
    dupes = db.execute(
        """SELECT hall_id, day_of_week, meal, is_extra, position, COUNT(*) c
             FROM menu_item
            GROUP BY 1,2,3,4,5 HAVING c > 1""").fetchall()
    assert dupes == []


def test_shrinking_menu_removes_stale_items(tmp_path, weekly):
    """If upstream drops items, the old rows must not survive the rebuild."""
    conn = connect(tmp_path / "shrink.db")
    ingest(conn, HALLS, weekly)
    before = conn.execute("SELECT COUNT(*) FROM menu_item").fetchone()[0]

    shrunk = {hid: [dict(r, description="Dal", extras=None) for r in rows]
              for hid, rows in weekly.items()}
    ingest(conn, HALLS, shrunk)
    after = conn.execute("SELECT COUNT(*) FROM menu_item").fetchone()[0]
    assert after < before
    assert after == len(ROWS)
    conn.close()
