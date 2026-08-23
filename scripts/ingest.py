"""Entry point: python -m scripts.ingest

Reads the cached campusmess.in responses, parses them into canonical rows,
loads SQLite, and prints the coverage report.
"""

from __future__ import annotations

import argparse
import collections
import os
import sys

from ingestion import source
from ingestion.load import connect, coverage_report, ingest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Ingest cached mess menus into SQLite.")
    ap.add_argument("--db", default=os.environ.get("DB_PATH", "db/khana.db"))
    ap.add_argument("--cache", default=None, help="recon cache dir (default .notes/recon)")
    ap.add_argument("--threshold", type=float, default=85.0,
                    help="fuzzy clustering threshold (default 85)")
    ap.add_argument("--per-hall", action="store_true",
                    help="also print per-hall transcription density")
    args = ap.parse_args(argv)

    if args.cache:
        os.environ["RAW_CACHE_DIR"] = args.cache

    try:
        halls = source.load_halls()
        weekly = source.load_weekly()
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    conn = connect(args.db)
    items, slots, canon = ingest(conn, halls, weekly, cluster_threshold=args.threshold)
    print(f"ingested {len(items)} items from {len(weekly)} halls into {args.db}")
    print(coverage_report(conn))

    if args.per_hall:
        # Transcription density varies a lot between halls and that materially
        # affects negation questions. See .notes/phase_1.md A7.
        per = collections.Counter()
        slotn = collections.Counter()
        for i in items:
            per[i.hall_id] += 1
        for s in slots:
            slotn[s.hall_id] += 1
        names = {h["id"]: h["name"] for h in halls}
        print("\n  per-hall transcription density (items per slot):")
        for hid, n in sorted(per.items(), key=lambda kv: -kv[1] / max(1, slotn[kv[0]])):
            print(f"    {names.get(hid, hid):<9} {n / max(1, slotn[hid]):>5.1f}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
