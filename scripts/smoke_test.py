"""Phase 2 smoke test: run the D8 acceptance questions through naive RAG.

NOT an eval. It produces no accuracy figure and scores nothing — it exists to
confirm one query goes in and one cited, instrumented answer comes out, and to
show HOW the baseline fails on the questions where retrieval recall is 0%.
Scoring is Phase 3.
"""
from __future__ import annotations

import argparse
import time

from rag.pipeline import NaiveRAG
from scripts.retrieval_recall import QUESTIONS, gold_chunk_ids


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--sleep", type=float, default=4.0,
                    help="pause between queries (free-tier per-minute cap)")
    ap.add_argument("--only", type=int, nargs="*", help="run only these question numbers")
    args = ap.parse_args(argv)

    rag = NaiveRAG(k=args.k)
    specs = [q for q in QUESTIONS if not args.only or q["n"] in args.only]

    tot_p = tot_c = tot_t = 0
    for i, spec in enumerate(specs):
        gold = gold_chunk_ids(spec)
        r = rag.query(spec["q"])
        tot_p += r.prompt_tokens
        tot_c += r.completion_tokens
        tot_t += r.thinking_tokens

        print("=" * 74)
        print(f"Q{spec['n']}  {spec['category']}")
        print(f'  "{spec["q"]}"')
        if spec.get("note"):
            print(f"  note: {spec['note']}")
        print(f"  gold halls: {', '.join(spec['gold_halls'])}")
        if gold:
            got = [c for c in r.retrieved_chunk_ids if c in gold]
            print(f"  retrieval: {len(got)}/{len(gold)} gold chunks in prompt")
        print("-" * 74)
        print(r.answer)
        print("-" * 74)
        print("  cited:", ", ".join(f"[{c.n}] {c.hall} {c.day} {c.meal}"
                                    for c in r.citations) or "(none)")
        if r.dropped_citations:
            print(f"  DROPPED invented citations: {r.dropped_citations}")
        print("  retrieved:", ", ".join(
            ("*" if any(c.n == h.rank for c in r.citations) else "") + h.chunk.chunk_id
            for h in r.hits))
        print(" ", r.instrumentation_line())
        print()
        if i < len(specs) - 1:
            time.sleep(args.sleep)

    n = len(specs)
    print("=" * 74)
    print(f"TOTALS over {n} questions: prompt={tot_p}  completion={tot_c}  "
          f"thinking={tot_t}")
    print(f"  mean per query: prompt={tot_p/n:.0f}  completion={tot_c/n:.0f}  "
          f"thinking={tot_t/n:.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
