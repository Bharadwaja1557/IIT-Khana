"""D29-mandated re-check: does the no-hallucination property hold on Flash-Lite?

Phase 2 found the generator hallucinated nothing across the seven D8 acceptance
questions. D29 records that this is a property of the MODEL, not of RAG, and
requires re-running the check before the results table moves to a weaker model.
Do not inherit the finding silently.

Three hallucination measures, all mechanical rather than eyeballed:
  invented citations   an [n] not in the prompt
  phantom halls        a hall CLAIMED in the ANSWER line that appears in NO
                       retrieved chunk — the model asserting about data it
                       never saw
  missing answer line  the structured contract simply not honoured
"""
from __future__ import annotations

import time
from datetime import date

from rag.systems import NaiveRAG
from eval.grade import parse_halls
from scripts.retrieval_recall import QUESTIONS

AS_OF = date(2026, 8, 25)


def main() -> int:
    rag = NaiveRAG()
    tot_inv = tot_phantom = tot_noline = 0
    print("=" * 74)
    print(f"D29 RE-CHECK on {rag.llm.model} — 7 D8 acceptance questions")
    print("=" * 74)
    for i, spec in enumerate(QUESTIONS):
        a = rag.query(spec["q"], AS_OF)
        seen_halls = {c.split("__")[0] for c in a.retrieved_chunk_ids}
        claimed, had_line = parse_halls(a.answer)
        # map claimed hall names to chunk-id slugs
        phantom = sorted(h for h in claimed
                         if h.lower().replace(" ", "-") not in seen_halls)
        tot_inv += len(a.dropped_citations)
        tot_phantom += len(phantom)
        tot_noline += 0 if had_line else 1
        print(f"\nQ{spec['n']} {spec['category']}")
        print(f"  answer line present : {had_line}")
        print(f"  claimed halls       : {sorted(claimed) or '(none)'}")
        print(f"  halls actually shown: {sorted(seen_halls)}")
        print(f"  PHANTOM halls       : {phantom or 'none'}")
        print(f"  invented citations  : {a.dropped_citations or 'none'}")
        if i < len(QUESTIONS) - 1:
            time.sleep(3)

    print("\n" + "=" * 74)
    print(f"TOTALS over {len(QUESTIONS)} questions")
    print(f"  invented citations : {tot_inv}")
    print(f"  phantom halls      : {tot_phantom}")
    print(f"  missing ANSWER line: {tot_noline}")
    verdict = ("HOLDS — no fabrication detected" if not (tot_inv or tot_phantom)
               else "DOES NOT HOLD — fabrication present")
    print(f"  no-hallucination property: {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
