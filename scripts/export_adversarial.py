"""Build eval/labels/items_adversarial.csv — the C3 targeted label pass.

Why this exists: the C2 random sample of 150 has n=0 for the case that matters.
Unmarked non-veg is 11 of 1903 rows (0.6%), so random sampling was never going
to reach it. This sample is deliberately ADVERSARIAL.

    Bucket A  all unmarked items the tagger tagged `nonveg`
              -> measures PRECISION on the critical slice
    Bucket B  all items the tagger tagged `unclear`
              -> bare Momos / Pulao / Kolkatta Biryani is where hidden non-veg lives
    Bucket C  unmarked items tagged `veg` whose text contains an ambiguous DISH
              FORM -> the recall probe

Bucket C selects on dish form, NOT on protein words, deliberately: selecting on
protein words would define the probe by the tagger's own vocabulary, so it could
only ever find items the tagger already catches. Selecting on form asks a
different question — "of the dishes that COULD hide meat, how many do?" — which
the tagger's lexicon has no say in.

THIS FILE CANNOT PRODUCE AN OVERALL ACCURACY FIGURE and must never be pooled
with the random 150. See DECISIONS.md D20 and scripts/eval_adversarial.py.
"""

from __future__ import annotations

import argparse
import csv
import random
import re
import sqlite3
from pathlib import Path

from ingestion.tagger import tag

DB = Path("db/khana.db")
OUT = Path("eval/labels/items_adversarial.csv")

MARKER = re.compile(r"\(\s*non[\s-]*veg[^)]*\)|\bnon[\s-]*veg\b", re.I)

# Bucket C selector: DISH FORM, not protein. Kept deliberately independent of
# the tagger's _AMBIGUOUS and _NONVEG lexicons.
FORMS = [
    "biryani", "pulao", "pulav", "roll", "wrap", "momo", "noodles", "hakka",
    "schezwan", "manchurian", "fried rice", "cutlet", "kebab", "kabab",
    "seekh", "tikka", "sizzler", "kofta", "korma", "nugget", "finger",
    "popcorn", "65", "lollipop", "soup", "sandwich", "burger", "keema",
    "do pyaza",
]
FORM_RE = re.compile(r"(?:" + "|".join(re.escape(f) for f in FORMS) + r")", re.I)

C_CAP = 80
SEED = 20260824


def collect(conn):
    """-> {normalized: (count, example_raw, predicted_tag, marked)}"""
    agg: dict[str, dict] = {}
    for raw, norm in conn.execute("SELECT item_raw, item_normalized FROM menu_item"):
        e = agg.setdefault(norm, {"count": 0, "raws": []})
        e["count"] += 1
        e["raws"].append(raw)
    out = {}
    for norm, e in agg.items():
        # Prefer an unmarked raw as the example so the labeller sees the hard case.
        raws = sorted(e["raws"], key=lambda r: (bool(MARKER.search(r)), r))
        example = raws[0]
        # "marked" is a property of the EXAMPLE OCCURRENCE, not of the item.
        #
        # 6 of the 11 unmarked non-veg items also appear elsewhere WITH a marker
        # ("Tandoori Chicken (a)" and "Tandoori Chicken (Non-Veg)" both normalize
        # to 'tandoori chicken'). Treating the item as marked because some other
        # occurrence carries a marker would drop exactly the occurrences where
        # the tagger has to infer — which is the thing being measured.
        out[norm] = {
            "count": e["count"],
            "raw": example,
            "tag": tag(example),
            "marked": bool(MARKER.search(example)),
            "n_unmarked": sum(1 for r in e["raws"] if not MARKER.search(r)),
        }
    return out


def build(conn, cap=C_CAP, seed=SEED):
    items = collect(conn)
    rows, stats = [], {}

    # --- Bucket A: unmarked, tagged nonveg -> precision on the critical slice
    a = sorted(
        (n for n, e in items.items() if e["tag"] == "nonveg" and not e["marked"]),
        key=lambda n: (-items[n]["count"], n))
    stats["A"] = len(a)
    rows += [(n, "A_unmarked_nonveg") for n in a]

    # --- Bucket B: everything the tagger abstained on
    b = sorted((n for n, e in items.items() if e["tag"] == "unclear"),
               key=lambda n: (-items[n]["count"], n))
    stats["B"] = len(b)
    rows += [(n, "B_unclear") for n in b]

    # --- Bucket C: unmarked, tagged veg, ambiguous dish FORM -> recall probe
    c_all = sorted(
        (n for n, e in items.items()
         if e["tag"] == "veg" and not e["marked"] and FORM_RE.search(n)),
        key=lambda n: (-items[n]["count"], n))
    stats["C_precap"] = len(c_all)
    if len(c_all) > cap:
        c = sorted(random.Random(seed).sample(c_all, cap),
                   key=lambda n: (-items[n]["count"], n))
        stats["C_capped"] = True
    else:
        c = c_all
        stats["C_capped"] = False
    stats["C"] = len(c)
    rows += [(n, "C_form_probe") for n in c]

    return items, rows, stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)

    conn = sqlite3.connect(args.db)
    items, rows, stats = build(conn)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["item", "count", "stratum", "example_raw", "label"])
        for norm, stratum in rows:
            e = items[norm]
            # label column left BLANK, deliberately. Never pre-filled.
            w.writerow([norm, e["count"], stratum, e["raw"], ""])

    print(f"wrote {out}  ({len(rows)} rows, label column blank)")
    print()
    print("  bucket A  unmarked & tagged nonveg   "
          f"{stats['A']:>3}   precision on the critical slice")
    print(f"  bucket B  tagged unclear             {stats['B']:>3}   "
          "hidden non-veg probe")
    print(f"  bucket C  unmarked & tagged veg,     {stats['C']:>3}   recall probe"
          f"   (pre-cap {stats['C_precap']}"
          f"{', capped at %d, seed %d' % (C_CAP, SEED) if stats['C_capped'] else ', not capped'})")
    print(f"            ambiguous dish form")
    print(f"  {'':<38}{'-'*3}")
    print(f"  total to label                       {len(rows):>3}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
