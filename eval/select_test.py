"""Phase 3 B2–B6: select the 60 TEST questions and emit fuzzy candidates.

Selection is diversity-constrained, not just random: a category whose 10
questions all used the same predicate or the same slot would measure one fact
ten times. Caps are applied per predicate and per (day, meal).

Fuzzy-semantic gold is NOT computed here. The predicates are absent from the
corpus by construction, so no SQL can produce them. This emits CANDIDATE hall
sets from a documented keyword heuristic, explicitly flagged for hand-labelling.
The heuristic's output is a starting point for a human, never a label.
"""

from __future__ import annotations

import json
import random
import sqlite3
from collections import Counter
from pathlib import Path

DB = Path("db/khana.db")
OUT = Path("eval/questions")
SEED = 20260825
AS_OF = "2026-08-25"
TARGET = 10
CATEGORIES = ["lookup", "comparison", "aggregation", "negation", "temporal"]

MAX_PER_PREDICATE = 2
MAX_PER_SLOT = 2


def pick(cands, rng, target=TARGET):
    """Diversity-constrained sample: cap repeats of predicate and of (day, meal)."""
    pool = cands[:]
    rng.shuffle(pool)
    chosen, pred_n, slot_n = [], Counter(), Counter()
    for relax in (False, True):          # second pass drops caps if starved
        for c in pool:
            if len(chosen) >= target:
                break
            if c in chosen:
                continue
            p = c["predicate"].get("value", c["slot"].get("hall", "?"))
            s = (c["slot"]["day"], c["slot"]["meal"])
            if not relax and (pred_n[p] >= MAX_PER_PREDICATE
                              or slot_n[s] >= MAX_PER_SLOT):
                continue
            chosen.append(c)
            pred_n[p] += 1
            slot_n[s] += 1
    return chosen[:target]


# ------------------------------------------------------------ fuzzy (B3 flag)
# Predicates deliberately ABSENT from the corpus. `LIKE '%south indian%'`
# returns zero rows; the answer has to come from meaning. The keyword lists
# below are a HEURISTIC used only to propose a candidate set for a human to
# confirm or correct. They are documented in full so the proposal is auditable.
FUZZY = [
    ("south-indian breakfast", "Which halls do a South-Indian style breakfast on {day}?",
     "Breakfast", ["dosa", "idli", "uttapam", "vada", "sambhar", "upma", "pongal",
                   "coconut chutney", "nariyal chutney", "medu"]),
    ("something sweet", "Which halls have a proper dessert at {meal} on {day}?",
     "Dinner", ["halwa", "jalebi", "gulab jamun", "kheer", "rabdi", "ice cream",
                "custard", "laddu", "barfi", "sewai", "shahi tukda", "milk cake",
                "kalakand", "rasmalai", "chandrakala", "gujhiya", "malpua",
                "sandesh", "phirni", "cake", "brownie", "sweet"]),
    ("indo-chinese", "Which messes have Indo-Chinese food at {meal} on {day}?",
     "Dinner", ["manchurian", "noodles", "chowmein", "hakka", "schezwan",
                "chilli paneer", "chilli chicken", "honey chilli", "spring roll",
                "momo", "fried rice", "chilli potato"]),
    ("deep-fried snack", "Which halls have something deep-fried and snacky at {meal} on {day}?",
     "Dinner", ["pakoda", "pakora", "samosa", "kachori", "french fries", "fries",
                "bhujia", "vada", "tikki", "cutlet", "finger", "nugget", "puri",
                "poori", "fritter", "chips", "papad"]),
    ("light meal", "Which halls have a light, non-heavy {meal} on {day}?",
     "Dinner", ["khichdi", "dalia", "soup", "salad", "curd", "raita", "fruit",
                "steamed", "boiled", "upma", "poha", "idli"]),
    ("street food", "Which halls are doing street-food style items at {meal} on {day}?",
     "Dinner", ["chaat", "vada pav", "pav bhaji", "tikki", "golgappa", "pani puri",
                "bhel", "dahi puri", "kathi roll", "momo", "chowmein", "sev puri",
                "samosa", "kachori", "chole bhature"]),
    ("protein-heavy", "Which halls have a protein-heavy {meal} on {day}?",
     "Dinner", ["chicken", "mutton", "fish", "egg", "paneer", "soya", "chaap",
                "rajma", "chana", "chole", "dal"]),
    ("western", "Which halls have a Western or continental option at {meal} on {day}?",
     "Dinner", ["pasta", "pizza", "sandwich", "burger", "fries", "garlic bread",
                "white sauce", "red sauce", "cheese", "toast", "mayo", "salad"]),
    ("north-indian gravy", "Which halls have a rich North-Indian gravy dish at {meal} on {day}?",
     "Dinner", ["butter masala", "kadhai", "handi", "korma", "kofta", "makhani",
                "do pyaza", "rogan josh", "bhuna", "lababdar", "shahi", "pasanda",
                "kohlapuri", "jalfrezi", "tikka masala"]),
    ("comfort carbs", "Which halls have proper comfort-food carbs at {meal} on {day}?",
     "Dinner", ["paratha", "puri", "poori", "kulcha", "naan", "bhature",
                "khichdi", "biryani", "pulao", "fried rice", "litti"]),
]
# Slot is CHOSEN FOR DISCRIMINATION, not fixed arbitrarily: for each concept we
# scan every (day, meal) and take the slot whose candidate count sits nearest
# mid-band (7 of 14), subject to the D15 band and no slot reused. This is
# selecting a question, not tuning an answer — it happens before any system runs
# and D15 exists precisely to reject vacuous slots.
DAYS_ALL = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
            "Saturday", "Sunday"]
MEALS_ALL = ["Breakfast", "Lunch", "Dinner"]


def _count_hits(conn, day, meal, kws):
    rows = conn.execute("""
        SELECT h.name, m.item_raw FROM menu_item m JOIN hall h ON h.id=m.hall_id
         WHERE m.day_of_week=? AND m.meal=?""", (day, meal)).fetchall()
    hits = {}
    for hall, item in rows:
        low = item.lower()
        matched = [k for k in kws if k in low]
        if matched:
            hits.setdefault(hall, []).append({"item": item, "matched": matched})
    return hits


def _best_slot(conn, name, kws, taken, used_goldsets):
    """Nearest mid-band slot, subject to D15, no slot reused, AND no duplicate
    candidate hall set.

    The duplicate-goldset constraint was added after `south-indian breakfast`
    (Sat breakfast) and `light meal` (Sun breakfast) produced IDENTICAL hall sets
    — 7 halls, the same 7. Keyword overlap turned out NOT to be the cause
    (Jaccard 0.10; `light meal` fired on poha/curd/boiled, never on idli/upma).
    The real cause is that the same halls happen to serve breakfast items on both
    days. Two questions with identical gold carry the information of one, and a
    system could score both by luck, so distinct gold is enforced directly rather
    than hoping distinct concepts imply it.
    """
    meals = ["Breakfast"] if "breakfast" in name else MEALS_ALL
    scored = []
    for day in DAYS_ALL:
        for meal in meals:
            if (day, meal) in taken:
                continue
            hits = _count_hits(conn, day, meal, kws)
            n = len(hits)
            if 2 <= n <= 12 and frozenset(hits) not in used_goldsets:
                scored.append((abs(n - 7), day, meal, n))
    if not scored:
        return None
    scored.sort()
    return scored[0][1], scored[0][2], scored[0][3]


def fuzzy_candidates(conn):
    out = []
    taken = set()
    used_goldsets = set()
    for i, (name, tmpl, default_meal, kws) in enumerate(FUZZY, 1):
        best = _best_slot(conn, name, kws, taken, used_goldsets)
        if best is None:
            print(f"  !! fuzzy concept {name!r} has NO slot in the D15 band — dropped")
            continue
        day, meal, _n = best
        taken.add((day, meal))
        hits = _count_hits(conn, day, meal, kws)
        used_goldsets.add(frozenset(hits))
        out.append({
            "id": f"cand-fuzzy-{i:03d}", "category": "fuzzy_semantic",
            "q": tmpl.format(day=day, meal=meal.lower()),
            "as_of": AS_OF, "slot": {"day": day, "meal": meal},
            "predicate": {"kind": "semantic", "value": name,
                          "heuristic_keywords": kws},
            "gold": {"type": "hall_set", "halls": None,
                     "STATUS": "AWAITING_HAND_LABEL"},
            "candidate_gold": {
                "halls": sorted(hits),
                "n": len(hits),
                "evidence": {h: v for h, v in sorted(hits.items())},
                "WARNING": ("machine-proposed by keyword heuristic, NOT a label. "
                            "Requires human confirmation. The predicate does not "
                            "exist in the corpus, so no SQL can settle it."),
            },
            "sql": "N/A — predicate absent from corpus; see candidate_gold",
            "sql_params": [],
            "gate": {"rule": "D15", "n_halls": len(hits),
                     "passed": 2 <= len(hits) <= 12,
                     "note": "gate evaluated on the CANDIDATE set; must be "
                             "re-checked against the human label"},
        })
    return out


def main():
    rng = random.Random(SEED)
    conn = sqlite3.connect(DB)
    data = json.loads((OUT / "candidates.json").read_text())
    cands = data["candidates"]

    by_cat = {}
    for c in cands:
        by_cat.setdefault(c["category"], []).append(c)

    test = []
    print("=" * 72)
    print("B6  TEST set selection (diversity-constrained)")
    print("=" * 72)
    for i, cat in enumerate(CATEGORIES):
        # NOTE: one shared RNG, consumed in CATEGORIES order. This makes the
        # selection sensitive to candidate-pool sizes — changing any pool
        # reshuffles every category after it. Kept as-is deliberately: it is
        # what produced the reviewed set, and reproducibility of that set
        # outranks the tidier per-category seeding. Parse-artifact filtering is
        # therefore done surgically in eval/patch_lookup.py, not in the pool.
        sel = pick(by_cat[cat], rng)
        for j, c in enumerate(sel, 1):
            c = dict(c)
            c["id"] = f"test-{cat}-{j:02d}"
            test.append(c)
        preds = Counter(c["predicate"].get("value", "slot") for c in sel)
        slots = Counter(f"{c['slot']['day'][:3]}/{c['slot']['meal'][:3]}" for c in sel)
        print(f"  {cat:<13} {len(sel):>2} selected  "
              f"predicates={dict(preds)}")
        print(f"  {'':<13}    slots={dict(slots)}")

    fz = fuzzy_candidates(conn)
    print(f"  {'fuzzy_semantic':<13} {len(fz):>2} CANDIDATES for hand-labelling")
    for f in fz:
        print(f"  {'':<13}    {f['predicate']['value']:<24} "
              f"{f['slot']['day'][:3]}/{f['slot']['meal'][:3]}  "
              f"candidate n={f['candidate_gold']['n']:<2} "
              f"gate={'PASS' if f['gate']['passed'] else 'FAIL'}")

    (OUT / "test.json").write_text(json.dumps(
        {"set": "test", "scored": True, "seed": SEED, "as_of": AS_OF,
         "n": len(test), "questions": test}, indent=2))
    (OUT / "fuzzy_candidates.json").write_text(json.dumps(
        {"set": "fuzzy_candidates", "scored": False,
         "STATUS": "AWAITING_HAND_LABEL — gold intentionally null",
         "as_of": AS_OF, "questions": fz}, indent=2))

    print(f"\nwrote {OUT/'test.json'} ({len(test)} SQL-gold questions)")
    print(f"wrote {OUT/'fuzzy_candidates.json'} ({len(fz)} awaiting hand labels)")


if __name__ == "__main__":
    main()
