"""Score eval/labels/items_adversarial.csv — the C3 targeted pass.

THIS SAMPLE IS DELIBERATELY BIASED. It is constructed to over-represent the
0.6% of rows where the tagger must infer non-veg from unmarked text. It cannot
produce an overall accuracy figure, and it must NEVER be pooled with the random
150 in items_to_label.csv. Scored by a separate script, on purpose.

What it CAN measure:

  bucket A  precision on the critical slice — of the items the tagger called
            nonveg without a marker, how many really are
  bucket B  } the recall probe — of the true nonveg the tagger did NOT already
  bucket C  } call nonveg, how many exist and how many it caught

Note the asymmetry, which is the whole methodological point: bucket A was
selected BY the tagger's own predictions, so it can only measure precision.
Recall has to come from B and C, which were selected independently of what the
tagger predicted for them.
"""

from __future__ import annotations

import argparse
import collections
import csv
from pathlib import Path

from ingestion.tagger import TAGS, tag

LABELS = Path("eval/labels/items_adversarial.csv")
BUCKETS = ["A_unmarked_nonveg", "B_unclear", "C_form_probe"]


def load(path: Path):
    rows = []
    for r in csv.DictReader(path.open()):
        gold = r["label"].strip().lower()
        if not gold:
            continue
        rows.append({
            "item": r["item"], "count": int(r["count"]), "bucket": r["stratum"],
            "raw": r["example_raw"], "gold": gold, "pred": tag(r["example_raw"]),
        })
    return rows


def fmt(x):
    return "     n/a" if x is None else f"{100*x:7.1f}%"


def report(rows) -> str:
    L = []
    by = collections.defaultdict(list)
    for r in rows:
        by[r["bucket"]].append(r)

    L += ["=" * 70,
          "ADVERSARIAL EVALUATION  (C3)",
          "=" * 70,
          "",
          "  *** BIASED SAMPLE — NOT POOLED WITH THE RANDOM 150 ***",
          "  Constructed to over-represent unmarked non-veg (0.6% of rows).",
          "  No overall accuracy figure is computed, by design.",
          ""]

    # ---- composition -----------------------------------------------------
    L += ["SAMPLE COMPOSITION", ""]
    L.append(f"  {'bucket':<20} {'n':>4}   gold distribution")
    for b in BUCKETS:
        g = collections.Counter(r["gold"] for r in by[b])
        L.append(f"  {b:<20} {len(by[b]):>4}   " +
                 ", ".join(f"{k}={v}" for k, v in sorted(g.items())))
    L += ["", f"  total labelled       {len(rows):>4}", ""]

    # ---- 1. bucket A precision ------------------------------------------
    a = by["A_unmarked_nonveg"]
    a_true = [r for r in a if r["gold"] == "nonveg"]
    L += ["=" * 70,
          "1. BUCKET A PRECISION  (unmarked items the tagger called nonveg)",
          "=" * 70]
    L.append(f"  tagged nonveg without a marker : {len(a)}")
    L.append(f"  truly nonveg                   : {len(a_true)}")
    L.append(f"  PRECISION                      : {fmt(len(a_true)/len(a)) if a else 'n/a'}")
    wrong = [r for r in a if r["gold"] != "nonveg"]
    if wrong:
        L.append("  false positives:")
        for r in wrong:
            L.append(f"    gold={r['gold']:<7} {r['raw']!r}")
    else:
        L.append("  false positives                : none")

    # ---- 2. non-veg recall over B + C ------------------------------------
    bc = by["B_unclear"] + by["C_form_probe"]
    bc_true = [r for r in bc if r["gold"] == "nonveg"]
    caught = [r for r in bc_true if r["pred"] == "nonveg"]
    L += ["", "=" * 70,
          "2. NON-VEG RECALL  (buckets B + C, selected independently of prediction)",
          "=" * 70]
    L.append(f"  items probed                   : {len(bc)}")
    L.append(f"  TRUE NON-VEG FOUND             : {len(bc_true)}")
    if not bc_true:
        L += ["  RECALL                         : NOT ESTIMABLE (denominator 0)",
              "",
              "  Finding, not a failure: unmarked non-veg is confined to items",
              "  the tagger already catches."]
    else:
        L.append(f"  caught by the tagger           : {len(caught)}")
        L.append(f"  RECALL                         : "
                 f"{fmt(len(caught)/len(bc_true))}   (n={len(bc_true)})")
        if len(bc_true) < 5:
            L += ["",
                  f"  *** DENOMINATOR IS {len(bc_true)}. This is NOT a usable recall",
                  "  estimate. Report as bounded exposure, not as a percentage. ***"]
        for r in bc_true:
            status = "CAUGHT" if r["pred"] == "nonveg" else f"MISSED (pred={r['pred']})"
            L.append(f"    [{status}] {r['raw']!r}  x{r['count']}")

    # ---- 3. true non-veg per bucket --------------------------------------
    L += ["", "=" * 70, "3. TRUE NON-VEG BY BUCKET", "=" * 70]
    for b in BUCKETS:
        n = sum(1 for r in by[b] if r["gold"] == "nonveg")
        L.append(f"  {b:<20} {n:>3} of {len(by[b]):>3}")

    # ---- 4. per-class, biased-sample ------------------------------------
    L += ["", "=" * 70,
          "4. PER-CLASS OVER THE ADVERSARIAL SET  [BIASED SAMPLE — CONTEXT ONLY]",
          "=" * 70,
          "  These are NOT generalization figures. The sample was chosen to",
          "  over-represent hard cases; class priors here are nothing like the",
          "  corpus. Do not quote these as the tagger's performance.",
          "",
          f"  {'class':<9} {'precision':>10} {'recall':>9} {'gold n':>8} {'pred n':>8}"]
    for cls in TAGS:
        tp = sum(1 for r in rows if r["gold"] == cls and r["pred"] == cls)
        pn = sum(1 for r in rows if r["pred"] == cls)
        gn = sum(1 for r in rows if r["gold"] == cls)
        L.append(f"  {cls:<9} {fmt(tp/pn if pn else None):>10} "
                 f"{fmt(tp/gn if gn else None):>9} {gn:>8} {pn:>8}")

    L += ["", "  confusion (rows = gold, cols = predicted):",
          "  " + " " * 9 + "".join(f"{t:>9}" for t in TAGS)]
    for g in TAGS:
        L.append("  " + f"{g:<9}" + "".join(
            f"{sum(1 for r in rows if r['gold']==g and r['pred']==p):>9}" for p in TAGS))

    # ---- misses ----------------------------------------------------------
    errs = [r for r in rows if r["gold"] != r["pred"]]
    L += ["", "=" * 70, f"ALL DISAGREEMENTS ({len(errs)} of {len(rows)})", "=" * 70]
    for r in sorted(errs, key=lambda r: (r["bucket"], -r["count"])):
        kind = "ABSTAIN" if r["pred"] == "unclear" else "WRONG"
        L.append(f"  [{kind:<7}] {r['bucket'][:1]}  gold={r['gold']:<7} "
                 f"pred={r['pred']:<7} x{r['count']:<3} {r['raw']!r}")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default=str(LABELS))
    args = ap.parse_args(argv)
    rows = load(Path(args.labels))
    if not rows:
        print("no labelled rows")
        return 1
    print(report(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
