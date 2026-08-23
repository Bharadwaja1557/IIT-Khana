"""Export unique items for hand labelling. Phase 1 C1.

Writes two files:

  eval/labels/items_unlabeled.csv   every unique normalized item, label blank
  eval/labels/items_to_label.csv    150 sampled for hand labelling

The `label` column is left EMPTY in both. It is ground truth and belongs to the
maintainer (CLAUDE.md: "Ground truth in eval/ is mine"). Nothing here writes,
guesses, or suggests a label.

Sampling is stratified so the singleton tail is actually measured rather than
assumed:
  * 100 drawn frequency-weighted (without replacement) — covers the common
    items that dominate query answers
  * 50 drawn uniformly from items appearing exactly once — the tail is 68% of
    the vocabulary, so leaving it to chance would leave generalisation untested
"""

from __future__ import annotations

import argparse
import csv
import os
import random
import sqlite3
from pathlib import Path

OUT_DIR = Path("eval/labels")
SEED = 20260824  # fixed so the sample is reproducible


def fetch_items(db_path: str) -> list[tuple[str, int, str]]:
    """-> [(item_normalized, count, an example raw string)]"""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        """SELECT item_normalized, COUNT(*) AS n, MIN(item_raw)
             FROM menu_item
            GROUP BY item_normalized
            ORDER BY n DESC, item_normalized""").fetchall()
    conn.close()
    return rows


def sample(rows, n_weighted=100, n_tail=50, seed=SEED):
    rng = random.Random(seed)
    singles = [r for r in rows if r[1] == 1]
    multis = [r for r in rows if r[1] > 1]

    # Frequency-weighted without replacement, drawn from the whole vocabulary.
    pool = list(rows)
    weights = [r[1] for r in pool]
    weighted: list = []
    while pool and len(weighted) < n_weighted:
        pick = rng.choices(range(len(pool)), weights=weights, k=1)[0]
        weighted.append(pool.pop(pick))
        weights.pop(pick)

    chosen = {r[0] for r in weighted}
    tail_pool = [r for r in singles if r[0] not in chosen]
    tail = rng.sample(tail_pool, min(n_tail, len(tail_pool)))

    out = [(r, "weighted") for r in weighted] + [(r, "tail") for r in tail]
    out.sort(key=lambda x: (-x[0][1], x[0][0]))
    return out, len(singles), len(multis)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("DB_PATH", "db/khana.db"))
    ap.add_argument("--weighted", type=int, default=100)
    ap.add_argument("--tail", type=int, default=50)
    args = ap.parse_args(argv)

    rows = fetch_items(args.db)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    full = OUT_DIR / "items_unlabeled.csv"
    with full.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["item", "count", "label"])
        for item, n, _raw in rows:
            w.writerow([item, n, ""])

    picked, n_single, n_multi = sample(rows, args.weighted, args.tail)
    todo = OUT_DIR / "items_to_label.csv"
    with todo.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["item", "count", "stratum", "example_raw", "label"])
        for (item, n, raw), stratum in picked:
            w.writerow([item, n, stratum, raw, ""])

    mentions = sum(n for _, n, _ in rows)
    covered = sum(n for (_, n, _), _ in picked)
    print(f"vocabulary        {len(rows)} unique items over {mentions} mentions")
    print(f"  appearing once  {n_single} ({100*n_single/len(rows):.0f}%)")
    print(f"  appearing 2+    {n_multi}")
    print()
    print(f"wrote {full}  ({len(rows)} rows, label column blank)")
    print(f"wrote {todo}  ({len(picked)} rows: "
          f"{sum(1 for _, s in picked if s=='weighted')} weighted + "
          f"{sum(1 for _, s in picked if s=='tail')} tail)")
    print(f"  sample covers {covered}/{mentions} mentions ({100*covered/mentions:.0f}%)")
    print(f"  seed {SEED} (fixed; re-running reproduces the same sample)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
