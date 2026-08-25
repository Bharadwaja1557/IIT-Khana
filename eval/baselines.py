"""RULING 2: trivial baselines, as ROWS in the results table — not prose caveats.

No LLM calls. These are what a system must beat to have demonstrated anything.

  name-all-14   answer every hall.   Perfect recall, no discrimination.
  name-none     answer no halls.     Perfect precision on the empty set.
  random-2way   comparison only: pick one of the two named halls (50%).

The negation row is the reason this exists. Negation golds run to 11 and 12 of
14, so "name all 14 halls" scores ~0.92 F1 while being completely useless. A
prose caveat is easy to skim past; a row in the table sitting above the systems
is not.
"""

from __future__ import annotations

import json
from pathlib import Path

QDIR = Path("eval/questions")
HALL_SET_CATS = ["comparison", "aggregation", "negation", "temporal",
                 "fuzzy_semantic"]


def f1(pred: set, gold: set) -> float:
    if not pred and not gold:
        return 1.0
    if not pred or not gold:
        return 0.0
    tp = len(pred & gold)
    if not tp:
        return 0.0
    p, r = tp / len(pred), tp / len(gold)
    return 2 * p * r / (p + r)


def score(questions, all_halls):
    """-> {baseline: {category: (exact_match, mean_f1, n)}}"""
    out = {}
    for name in ("name-all-14", "name-none", "random-2way"):
        per = {}
        for q in questions:
            cat = q["category"]
            gold = set(q["gold"].get("halls") or [])
            if q["gold"]["type"] != "hall_set":
                continue
            if name == "name-all-14":
                pred = set(all_halls)
            elif name == "name-none":
                pred = set()
            else:
                if cat != "comparison":
                    continue
                # Expected value of a coin flip between the two named halls.
                cands = q["predicate"].get("candidates") or []
                em = sum(1 for c in cands if {c} == gold) / len(cands) if cands else 0.0
                per.setdefault(cat, []).append((em, em))
                continue
            per.setdefault(cat, []).append(
                (1.0 if pred == gold else 0.0, f1(pred, gold)))
        out[name] = {c: (sum(x for x, _ in v) / len(v),
                         sum(y for _, y in v) / len(v), len(v))
                     for c, v in per.items()}
    return out


def main() -> int:
    test = json.loads((QDIR / "test.json").read_text())
    import sqlite3
    all_halls = [r[0] for r in sqlite3.connect("db/khana.db")
                 .execute("SELECT name FROM hall")]

    res = score(test["questions"], all_halls)

    print("=" * 74)
    print("TRIVIAL BASELINES — no LLM, no retrieval. The floor to beat.")
    print("=" * 74)
    print(f"  {'baseline':<14}{'category':<16}{'exact':>8}{'F1':>8}{'n':>5}")
    for name, per in res.items():
        for cat in HALL_SET_CATS:
            if cat not in per:
                continue
            em, mf1, n = per[cat]
            flag = "   <-- useless strategy scoring high" if mf1 >= 0.85 else ""
            print(f"  {name:<14}{cat:<16}{100*em:>7.0f}%{mf1:>8.2f}{n:>5}{flag}")
        print()

    # gold-size context, which is what drives the name-all row
    print("  gold set sizes (drives the name-all-14 F1):")
    for cat in HALL_SET_CATS:
        sizes = [q["gold"]["n"] for q in test["questions"]
                 if q["category"] == cat and q["gold"]["type"] == "hall_set"]
        if sizes:
            print(f"    {cat:<16} n={sorted(sizes)}  mean={sum(sizes)/len(sizes):.1f}")

    (QDIR / "baselines.json").write_text(json.dumps(res, indent=2))
    print(f"\nwrote {QDIR/'baselines.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
