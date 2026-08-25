"""Agreement between the hand labels and the SEALED machine candidates.

This decides something real. If agreement is near-total, the fuzzy-semantic
category is LEXICAL — a keyword list reproduces a human's semantic judgement —
and D10's justification for keeping a retrieval path is weaker than assumed,
because the "predicate absent from the corpus" argument would be doing no work.
If agreement is partial, the category is genuinely semantic and the second path
has something to earn.

Reported either way. Nothing here modifies a label.
"""

from __future__ import annotations

import json
from pathlib import Path

QDIR = Path("eval/questions")


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


def main() -> int:
    labels = {q["id"].replace("label-", ""): q
              for q in json.loads((QDIR / "fuzzy_for_label.json").read_text())["questions"]}
    cands = {q["id"].replace("cand-", ""): q
             for q in json.loads((QDIR / "fuzzy_candidates.json").read_text())["questions"]}

    rows, skipped = [], []
    for key in sorted(labels):
        lab = labels[key]["label_halls"]
        if lab is None:
            skipped.append(key)
            continue
        human = set(lab)
        machine = set(cands[key]["candidate_gold"]["halls"])
        rows.append({
            "id": key,
            "concept": cands[key]["predicate"]["value"],
            "slot": f"{lab if False else labels[key]['slot']['day'][:3]}/"
                    f"{labels[key]['slot']['meal'][:3]}",
            "human": sorted(human), "machine": sorted(machine),
            "n_h": len(human), "n_m": len(machine),
            "exact": human == machine,
            "f1": f1(machine, human),
            "machine_only": sorted(machine - human),
            "human_only": sorted(human - machine),
        })

    print("=" * 78)
    print("FUZZY AGREEMENT — hand labels vs SEALED machine candidates")
    print("=" * 78)
    print(f"  {'id':<12}{'concept':<24}{'slot':<9}{'nH':>3}{'nM':>4}{'exact':>7}{'F1':>7}")
    for r in rows:
        print(f"  {r['id']:<12}{r['concept']:<24}{r['slot']:<9}"
              f"{r['n_h']:>3}{r['n_m']:>4}{'YES' if r['exact'] else 'no':>7}{r['f1']:>7.2f}")

    n = len(rows)
    ex = sum(r["exact"] for r in rows)
    mf1 = sum(r["f1"] for r in rows) / n
    print(f"\n  OVERALL  exact-match {ex}/{n} = {100*ex/n:.0f}%     mean F1 {mf1:.3f}")
    if skipped:
        print(f"  SKIPPED (label_halls null, not labelled by hand): {skipped}")

    print("\n  disagreements, per question:")
    for r in rows:
        if r["exact"]:
            continue
        print(f"    {r['id']} ({r['concept']}):")
        if r["machine_only"]:
            print(f"       machine said, human did NOT: {r['machine_only']}")
        if r["human_only"]:
            print(f"       human said, machine did NOT: {r['human_only']}")

    (QDIR / "fuzzy_agreement.json").write_text(json.dumps(
        {"n_compared": n, "exact_match": ex, "exact_rate": ex / n,
         "mean_f1": mf1, "skipped": skipped, "rows": rows}, indent=2))
    print(f"\nwrote {QDIR/'fuzzy_agreement.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
