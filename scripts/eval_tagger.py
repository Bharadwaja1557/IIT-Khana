"""Evaluate the diet tagger against hand labels. Phase 1 C2.

Four evaluations, all reported:
  mention-level  weighted by occurrence count  -> governs query correctness
  type-level     unweighted over unique items  -> the generalization number
  marked subset  raw string carries an explicit (Non-Veg) marker
  unmarked subset  no marker; the tagger actually has to infer

Abstentions (`unclear`) are scored as ERRORS against a definite gold label.
That is intended: it is the real cost of abstaining. Abstention rate is reported
separately so the two are never conflated.
"""

from __future__ import annotations

import argparse
import collections
import csv
import re
from pathlib import Path

from ingestion.tagger import TAGS, tag

LABELS = Path("eval/labels/items_to_label.csv")
MARKER = re.compile(r"\(\s*non[\s-]*veg[^)]*\)|\bnon[\s-]*veg\b|\(\s*veg\s*\)", re.I)


def load_labels(path: Path = LABELS):
    rows = []
    for r in csv.DictReader(path.open()):
        gold = r["label"].strip().lower()
        if not gold:
            continue
        rows.append({
            "item": r["item"],
            "count": int(r["count"]),
            "stratum": r["stratum"],
            "raw": r["example_raw"],
            "gold": gold,
            "marked": bool(MARKER.search(r["example_raw"])),
        })
    return rows


def score(rows, weighted: bool):
    """-> (accuracy, n, abstention_rate). Weight is occurrence count or 1."""
    tot = right = abst = 0
    for r in rows:
        w = r["count"] if weighted else 1
        tot += w
        pred = tag(r["raw"])
        if pred == r["gold"]:
            right += w
        if pred == "unclear":
            abst += w
    return (right / tot if tot else 0.0), tot, (abst / tot if tot else 0.0)


def per_class(rows):
    """Precision/recall/F1 per gold class, plus support."""
    stats = {}
    for cls in TAGS:
        tp = sum(1 for r in rows if r["gold"] == cls and tag(r["raw"]) == cls)
        fp = sum(1 for r in rows if r["gold"] != cls and tag(r["raw"]) == cls)
        fn = sum(1 for r in rows if r["gold"] == cls and tag(r["raw"]) != cls)
        prec = tp / (tp + fp) if tp + fp else None
        rec = tp / (tp + fn) if tp + fn else None
        f1 = (2 * prec * rec / (prec + rec)) if prec and rec else None
        stats[cls] = (prec, rec, f1, tp + fn)
    return stats


def confusion(rows):
    c = collections.Counter((r["gold"], tag(r["raw"])) for r in rows)
    return c


def majority_baseline(rows, weighted: bool):
    """Always predict the commonest gold class. The number the tagger must beat."""
    w = collections.Counter()
    for r in rows:
        w[r["gold"]] += r["count"] if weighted else 1
    if not w:
        return 0.0, None
    cls, hits = w.most_common(1)[0]
    return hits / sum(w.values()), cls


def fmt_pct(x):
    return "  n/a " if x is None else f"{100*x:5.1f}%"


def report(rows) -> str:
    L = []
    n = len(rows)
    marked = [r for r in rows if r["marked"]]
    unmarked = [r for r in rows if not r["marked"]]
    mentions = sum(r["count"] for r in rows)

    L += ["=" * 68, "TAGGER EVALUATION", "=" * 68, ""]

    # -- label set composition --------------------------------------------
    gold_types = collections.Counter(r["gold"] for r in rows)
    gold_ment = collections.Counter()
    for r in rows:
        gold_ment[r["gold"]] += r["count"]
    L += ["LABEL SET", f"  labelled items          {n}",
          f"  mentions covered        {mentions}", ""]
    L += ["  gold distribution       types      mentions"]
    for cls in TAGS:
        L.append(f"    {cls:<10} {gold_types.get(cls,0):>10}  "
                 f"{gold_ment.get(cls,0):>12}")
    if gold_types.get("unclear", 0) == 0:
        L += ["", "  NOTE: zero items were labelled `unclear`. See DECISIONS.md D21."]

    # -- marked / unmarked split ------------------------------------------
    L += ["", "MARKED / UNMARKED SPLIT",
          f"  marked (explicit diet marker in raw)    {len(marked):>3}  "
          f"({100*len(marked)/n:.0f}% of labelled items)",
          f"  unmarked (tagger must infer)           {len(unmarked):>3}  "
          f"({100*len(unmarked)/n:.0f}%)"]
    mg = collections.Counter(r["gold"] for r in marked)
    ug = collections.Counter(r["gold"] for r in unmarked)
    L.append(f"    marked gold:   " + ", ".join(f"{k}={v}" for k, v in sorted(mg.items())))
    L.append(f"    unmarked gold: " + ", ".join(f"{k}={v}" for k, v in sorted(ug.items())))

    # -- the four evaluations ---------------------------------------------
    L += ["", "=" * 68, "ACCURACY", "=" * 68,
          f"  {'evaluation':<26} {'n':>6} {'accuracy':>9} {'abstain':>9} {'majority':>9}"]
    for label, subset, weighted in [
        ("mention-level (all)", rows, True),
        ("type-level (all)", rows, False),
        ("marked subset", marked, False),
        ("unmarked subset", unmarked, False),
        ("unmarked, mention-wtd", unmarked, True),
    ]:
        if not subset:
            L.append(f"  {label:<26} {'-':>6} {'(empty)':>9}")
            continue
        acc, tot, abst = score(subset, weighted)
        base, _cls = majority_baseline(subset, weighted)
        L.append(f"  {label:<26} {tot:>6} {fmt_pct(acc):>9} "
                 f"{fmt_pct(abst):>9} {fmt_pct(base):>9}")

    L += ["", "  'majority' = always predict the commonest gold class.",
          "  Abstentions count as errors in the accuracy column."]

    # -- per class ---------------------------------------------------------
    L += ["", "=" * 68, "PER-CLASS (type-level, all labelled items)", "=" * 68,
          f"  {'class':<10} {'precision':>10} {'recall':>8} {'F1':>8} {'support':>9}"]
    for cls, (p, r_, f1, sup) in per_class(rows).items():
        L.append(f"  {cls:<10} {fmt_pct(p):>10} {fmt_pct(r_):>8} "
                 f"{fmt_pct(f1):>8} {sup:>9}")

    # -- confusion ---------------------------------------------------------
    c = confusion(rows)
    L += ["", "=" * 68, "CONFUSION MATRIX (rows = gold, cols = predicted)", "=" * 68,
          "  " + " " * 10 + "".join(f"{t:>9}" for t in TAGS) + f"{'total':>9}"]
    for g in TAGS:
        row = [c.get((g, p), 0) for p in TAGS]
        tot = sum(row)
        mark = "   <- no gold items in this class" if tot == 0 else ""
        L.append("  " + f"{g:<10}" + "".join(f"{v:>9}" for v in row) + f"{tot:>9}" + mark)

    # -- errors ------------------------------------------------------------
    errs = [(r, tag(r["raw"])) for r in rows if tag(r["raw"]) != r["gold"]]
    L += ["", "=" * 68, f"ERRORS ({len(errs)} of {n})", "=" * 68]
    if not errs:
        L.append("  none")
    for r, pred in sorted(errs, key=lambda x: -x[0]["count"]):
        kind = "ABSTAIN" if pred == "unclear" else "WRONG"
        L.append(f"  [{kind:<7}] gold={r['gold']:<7} pred={pred:<7} "
                 f"x{r['count']:<3} {r['raw']!r}")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default=str(LABELS))
    args = ap.parse_args(argv)
    rows = load_labels(Path(args.labels))
    if not rows:
        print("no labelled rows found")
        return 1
    print(report(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
