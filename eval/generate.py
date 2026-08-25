"""Phase 3 B2–B5: generate TEST question candidates, compute gold by SQL, gate.

Order enforced here, per the brief:
  B2  template structure from the DB, vary surface phrasing, generate ~4x surplus
  B3  gold answers computed by SQL — never by hand, never by model. The query is
      stored alongside every question so it is auditable.
  B4  D15 discrimination gate, applied programmatically
  B5  D14 vocabulary partition — negation may only use meaningful-absence items,
      rejected at GENERATION time, not by review

Fuzzy-semantic gold cannot be SQL-derived (the predicates are absent from the
corpus). Those emit CANDIDATE sets flagged for hand-labelling and are never
labelled here.
"""

from __future__ import annotations

import json
import random
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, asdict, field
from pathlib import Path

DB = Path("db/khana.db")
OUT = Path("eval/questions")
SEED = 20260825
AS_OF = "2026-08-25"          # a Tuesday. Pinned so temporal gold is reproducible.

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MEALS = ["Breakfast", "Lunch", "Dinner"]
N_HALLS = 14
TARGET_PER_CATEGORY = 10

# ---------------------------------------------------------------- D14 (B5)
# Group 1: assumed-present staples. Absence proves NOTHING, so a negation
# question may never use one. Rejected at generation time.
D14_STAPLE = re.compile(
    r"^(plain\s+)?(rice|chawal|roti|chapati|chapatti|phulka|dal|daal|curd|dahi|"
    r"raita|buttermilk|chaach|chhach|chhanch|mattha|water|pani|paani)$", re.I)
D14_STAPLE_PREDICATE = {
    "rice", "roti", "chapati", "dal", "daal", "curd", "dahi", "raita",
    "buttermilk", "water", "chutney", "salad", "papad", "pickle", "fruit",
    "fruits", "lassi", "nimbu pani", "juice", "milk", "tea", "coffee",
}

# ------------------------------------------------------- predicate vocabulary
# Ingredient / dish-family predicates. Exact canonical items are too sparse:
# 1,254 of 1,495 (day, meal, canonical) tuples appear in exactly ONE hall, a
# direct consequence of the Phase 1 long tail. Substring predicates are what
# actually discriminate.
PREDICATES = [
    # (predicate string used in SQL LIKE, natural noun for phrasing, D14 group)
    ("chicken", "chicken", 2), ("paneer", "paneer", 2), ("fish", "fish", 2),
    ("mutton", "mutton", 2), ("egg", "egg", 2), ("dosa", "dosa", 2),
    ("idli", "idli", 2), ("biryani", "biryani", 2), ("jalebi", "jalebi", 2),
    ("halwa", "halwa", 2), ("rajma", "rajma", 2), ("poha", "poha", 2),
    ("gulab jamun", "gulab jamun", 2), ("sambhar", "sambhar", 2),
    ("noodles", "noodles", 2), ("pasta", "pasta", 2), ("momo", "momos", 2),
    ("kheer", "kheer", 2), ("ice cream", "ice cream", 2),
    ("french fries", "french fries", 2), ("chole", "chole", 2),
    ("uttapam", "uttapam", 2), ("pulao", "pulao", 2), ("kofta", "kofta", 2),
    ("tikka", "tikka", 2), ("pakoda", "pakoda", 2), ("vada", "vada", 2),
    ("chaat", "chaat", 2), ("manchurian", "manchurian", 2),
    ("soyabean", "soya", 2), ("mushroom", "mushroom", 2), ("kadhi", "kadhi", 2),
]
TAG_PREDICATES = [("nonveg", "non-veg"), ("egg", "egg-based")]


@dataclass
class Question:
    id: str
    category: str
    q: str
    as_of: str
    slot: dict
    predicate: dict
    gold: dict
    sql: str
    sql_params: list
    gate: dict
    notes: str = ""


# --------------------------------------------------------------------- SQL

SQL_HALLS_WITH_LIKE = """
SELECT DISTINCT h.name
  FROM menu_item m JOIN hall h ON h.id = m.hall_id
 WHERE m.day_of_week = ? AND m.meal = ? AND LOWER(m.item_raw) LIKE ?
 ORDER BY h.name
"""
SQL_HALLS_WITHOUT_LIKE = """
SELECT h.name FROM hall h
 WHERE h.id NOT IN (
   SELECT m.hall_id FROM menu_item m
    WHERE m.day_of_week = ? AND m.meal = ? AND LOWER(m.item_raw) LIKE ?)
 ORDER BY h.name
"""
SQL_COMPARISON = """
SELECT DISTINCT h.name
  FROM menu_item m JOIN hall h ON h.id = m.hall_id
 WHERE m.day_of_week = ? AND m.meal = ? AND LOWER(m.item_raw) LIKE ?
   AND h.name IN (?, ?)
 ORDER BY h.name
"""
SQL_HALLS_WITH_TAG = """
SELECT DISTINCT h.name
  FROM menu_item m JOIN hall h ON h.id = m.hall_id
 WHERE m.day_of_week = ? AND m.meal = ? AND m.tags = ?
 ORDER BY h.name
"""
SQL_SLOT_ITEMS = """
SELECT m.item_raw, m.is_extra
  FROM menu_item m JOIN hall h ON h.id = m.hall_id
 WHERE h.name = ? AND m.day_of_week = ? AND m.meal = ?
 ORDER BY m.is_extra, m.position
"""
SQL_DISTRACTORS = """
SELECT DISTINCT m.item_raw
  FROM menu_item m JOIN hall h ON h.id = m.hall_id
 WHERE m.day_of_week = ? AND m.meal = ? AND h.name <> ?
"""


def q_all(conn, sql, params):
    return [r[0] for r in conn.execute(sql, params).fetchall()]


# ------------------------------------------------------------- phrasing (B2)

TEMPLATES = {
    "aggregation": [
        "Which halls serve {noun} at {meal} on {day}?",
        "How many halls have {noun} for {meal} on {day}?",
        "List the halls with {noun} on the {day} {meal} menu.",
        "On {day}, which messes are serving {noun} at {meal}?",
        "Who's got {noun} for {meal} on {day}?",
    ],
    "negation": [
        "Which halls do NOT serve {noun} at {meal} on {day}?",
        "Which messes have no {noun} for {meal} on {day}?",
        "On {day}, which halls are missing {noun} from the {meal} menu?",
        "If I want to avoid {noun} at {meal} on {day}, which halls work?",
        "Which halls skip {noun} for {meal} on {day}?",
    ],
    "temporal": [
        "Which halls have {noun} for dinner tonight?",
        "Which messes are serving {noun} at lunch today?",
        "Who has {noun} for dinner tomorrow?",
        "Is anyone doing {noun} for breakfast tomorrow?",
        "Which halls have {noun} tonight at dinner?",
    ],
    "lookup": [
        "What's for {meal} at {hall} on {day}?",
        "What is {hall} serving at {meal} on {day}?",
        "Show me the {day} {meal} menu for {hall}.",
        "What's on the menu at {hall}, {day} {meal}?",
    ],
    "comparison": [
        "Between {hall_a} and {hall_b}, which serves {noun} at {meal} on {day}?",
        "For {meal} on {day}, does {hall_a} or {hall_b} have {noun}?",
        "I want {noun} at {meal} on {day} — {hall_a} or {hall_b}?",
        "Which of {hall_a} or {hall_b} has {noun} on the {day} {meal} menu?",
    ],
}

# Relative-day templates need the resolved day to match the target slot.
TEMPORAL_SPEC = [
    # (template index, required day offset from AS_OF, required meal)
    (0, 0, "Dinner"), (1, 0, "Lunch"), (2, 1, "Dinner"),
    (3, 1, "Breakfast"), (4, 0, "Dinner"),
]


def build(conn, rng):
    cand: list[Question] = []
    rejects = Counter()
    seen = set()

    halls = q_all(conn, "SELECT name FROM hall ORDER BY name", ())
    as_of_day = "Tuesday"                       # AS_OF 2026-08-25 is a Tuesday
    as_of_idx = DAYS.index(as_of_day)

    def gate_hallset(n):
        return 2 <= n <= N_HALLS - 2            # D15: 2..12 of 14

    # ---------------------------------------------------- aggregation (B3/B4)
    n = 0
    for day in DAYS:
        for meal in MEALS:
            for pred, noun, group in PREDICATES:
                key = ("aggregation", day, meal, pred)
                if key in seen:
                    continue
                params = [day, meal, f"%{pred}%"]
                got = q_all(conn, SQL_HALLS_WITH_LIKE, params)
                if not gate_hallset(len(got)):
                    rejects["aggregation"] += 1
                    continue
                seen.add(key)
                n += 1
                cand.append(Question(
                    id=f"cand-aggregation-{n:03d}", category="aggregation",
                    q=rng.choice(TEMPLATES["aggregation"]).format(
                        noun=noun, meal=meal.lower(), day=day),
                    as_of=AS_OF, slot={"day": day, "meal": meal},
                    predicate={"kind": "like", "value": pred},
                    gold={"type": "hall_set", "halls": got, "n": len(got)},
                    sql=SQL_HALLS_WITH_LIKE.strip(), sql_params=params,
                    gate={"rule": "D15", "n_halls": len(got), "passed": True}))

    # ------------------------------------------------------ negation (B4/B5)
    n = 0
    for day in DAYS:
        for meal in MEALS:
            for pred, noun, group in PREDICATES:
                # B5: D14 partition enforced at GENERATION time.
                if pred in D14_STAPLE_PREDICATE or D14_STAPLE.match(pred):
                    rejects["negation_d14"] += 1
                    continue
                key = ("negation", day, meal, pred)
                if key in seen:
                    continue
                params = [day, meal, f"%{pred}%"]
                without = q_all(conn, SQL_HALLS_WITHOUT_LIKE, params)
                with_ = q_all(conn, SQL_HALLS_WITH_LIKE, params)
                # Both sides must be non-trivial: gold in band AND at least 2
                # halls actually serve it, else "none serve it" is vacuous.
                if not gate_hallset(len(without)) or len(with_) < 2:
                    rejects["negation"] += 1
                    continue
                seen.add(key)
                n += 1
                cand.append(Question(
                    id=f"cand-negation-{n:03d}", category="negation",
                    q=rng.choice(TEMPLATES["negation"]).format(
                        noun=noun, meal=meal.lower(), day=day),
                    as_of=AS_OF, slot={"day": day, "meal": meal},
                    predicate={"kind": "not_like", "value": pred},
                    gold={"type": "hall_set", "halls": without, "n": len(without)},
                    sql=SQL_HALLS_WITHOUT_LIKE.strip(), sql_params=params,
                    gate={"rule": "D15+D14", "n_halls": len(without),
                          "n_serving": len(with_), "passed": True},
                    notes="D14 group-2 predicate only"))

    # ------------------------------------------------------------- temporal
    n = 0
    for ti, (tmpl_i, offset, meal) in enumerate(TEMPORAL_SPEC):
        day = DAYS[(as_of_idx + offset) % 7]
        for pred, noun, group in PREDICATES:
            key = ("temporal", day, meal, pred, tmpl_i)
            if key in seen:
                continue
            params = [day, meal, f"%{pred}%"]
            got = q_all(conn, SQL_HALLS_WITH_LIKE, params)
            if not gate_hallset(len(got)):
                rejects["temporal"] += 1
                continue
            seen.add(key)
            n += 1
            cand.append(Question(
                id=f"cand-temporal-{n:03d}", category="temporal",
                q=TEMPLATES["temporal"][tmpl_i].format(noun=noun),
                as_of=AS_OF, slot={"day": day, "meal": meal},
                predicate={"kind": "like", "value": pred},
                gold={"type": "hall_set", "halls": got, "n": len(got)},
                sql=SQL_HALLS_WITH_LIKE.strip(), sql_params=params,
                gate={"rule": "D15", "n_halls": len(got), "passed": True},
                notes=f"relative day must resolve to {day} {meal} given as_of={AS_OF}"))

    # --------------------------------------------------------------- lookup
    # D15's 2..12 band cannot apply: a lookup names ONE hall by construction
    # and its answer is an ITEM set, not a hall set. Substitute gate: the slot
    # must hold >= 3 items, so the answer is non-trivial. FLAGGED FOR REVIEW.
    n = 0
    for hall in halls:
        for day in DAYS:
            for meal in MEALS:
                key = ("lookup", hall, day, meal)
                if key in seen:
                    continue
                rows = conn.execute(SQL_SLOT_ITEMS, (hall, day, meal)).fetchall()
                if len(rows) < 3:
                    rejects["lookup"] += 1
                    continue
                seen.add(key)
                n += 1
                distract = q_all(conn, SQL_DISTRACTORS, (day, meal, hall))
                gold_items = [r[0] for r in rows]
                cand.append(Question(
                    id=f"cand-lookup-{n:03d}", category="lookup",
                    q=rng.choice(TEMPLATES["lookup"]).format(
                        hall=hall, meal=meal.lower(), day=day),
                    as_of=AS_OF, slot={"day": day, "meal": meal, "hall": hall},
                    predicate={"kind": "slot_items"},
                    gold={"type": "item_set", "items": gold_items,
                          "base": [r[0] for r in rows if not r[1]],
                          "extras": [r[0] for r in rows if r[1]],
                          "n": len(gold_items),
                          "distractors": sorted(set(distract) - set(gold_items))},
                    sql=SQL_SLOT_ITEMS.strip(), sql_params=[hall, day, meal],
                    gate={"rule": "substitute-for-D15", "n_items": len(gold_items),
                          "passed": True}))

    # ----------------------------------------------------------- comparison
    # Same issue: two halls are NAMED in the question. Substitute gate — the two
    # halls must DIFFER on the predicate, so exactly one is correct and "both"
    # / "neither" are wrong. FLAGGED FOR REVIEW.
    n = 0
    for day in DAYS:
        for meal in MEALS:
            for pred, noun, group in PREDICATES:
                params = [day, meal, f"%{pred}%"]
                with_ = set(q_all(conn, SQL_HALLS_WITH_LIKE, params))
                without = set(halls) - with_
                if not with_ or not without:
                    rejects["comparison"] += 1
                    continue
                a = rng.choice(sorted(with_))
                b = rng.choice(sorted(without))
                key = ("comparison", day, meal, pred, a, b)
                if key in seen:
                    continue
                seen.add(key)
                n += 1
                pair = [a, b]
                rng.shuffle(pair)
                cand.append(Question(
                    id=f"cand-comparison-{n:03d}", category="comparison",
                    q=rng.choice(TEMPLATES["comparison"]).format(
                        hall_a=pair[0], hall_b=pair[1], noun=noun,
                        meal=meal.lower(), day=day),
                    as_of=AS_OF, slot={"day": day, "meal": meal},
                    predicate={"kind": "like", "value": pred,
                               "candidates": pair},
                    gold={"type": "hall_set", "halls": [a], "n": 1},
                    # SQL restricted to the two named halls, so the stored query
                    # produces the gold answer directly rather than needing an
                    # intersection the reader has to perform mentally.
                    sql=SQL_COMPARISON.strip(),
                    sql_params=params + [pair[0], pair[1]],
                    gate={"rule": "substitute-for-D15",
                          "differ": True, "passed": True},
                    notes="exactly one of the two named halls serves it"))

    return cand, rejects


def main():
    rng = random.Random(SEED)
    conn = sqlite3.connect(DB)
    cand, rejects = build(conn, rng)

    by_cat = {}
    for c in cand:
        by_cat.setdefault(c.category, []).append(c)

    print("=" * 72)
    print("B2–B5  candidate generation, gold by SQL, gates applied")
    print("=" * 72)
    print(f"  {'category':<14}{'generated':>10}{'rejected':>10}{'rate':>8}{'kept':>7}")
    total_gen = total_rej = 0
    for cat in ["lookup", "comparison", "aggregation", "negation", "temporal"]:
        kept = len(by_cat.get(cat, []))
        rej = rejects[cat] + (rejects["negation_d14"] if cat == "negation" else 0)
        gen = kept + rej
        total_gen += gen
        total_rej += rej
        print(f"  {cat:<14}{gen:>10}{rej:>10}{100*rej/gen if gen else 0:>7.0f}%{kept:>7}")
    print(f"  {'TOTAL':<14}{total_gen:>10}{total_rej:>10}"
          f"{100*total_rej/total_gen:>7.0f}%{len(cand):>7}")
    if rejects["negation_d14"]:
        print(f"\n  of which D14 staple rejections (B5): {rejects['negation_d14']}")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "candidates.json").write_text(json.dumps(
        {"seed": SEED, "as_of": AS_OF,
         "rejects": dict(rejects),
         "candidates": [asdict(c) for c in cand]}, indent=2))
    print(f"\nwrote {OUT/'candidates.json'} ({len(cand)} candidates)")
    return by_cat


if __name__ == "__main__":
    main()
