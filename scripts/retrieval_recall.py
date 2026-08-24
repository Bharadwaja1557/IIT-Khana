"""Retrieval-only diagnostics for the D8 acceptance questions. Zero LLM calls.

This measures the ceiling the naive RAG baseline operates under, independent of
whatever the generator does with the chunks. If a gold chunk was never
retrieved, no prompt and no model can recover the answer from it.

NOT a scoring harness — it never looks at an answer. Answer scoring is Phase 3.

Two different things are measured, because they fail differently:

  positive recall   did the chunks containing the answer entities get retrieved?
  slot coverage     for count/negation questions, how many of the 14 halls in the
                    target (day, meal) slot were retrieved at all? A question like
                    "how many halls serve paneer at dinner on Tuesday" cannot be
                    answered correctly without seeing every hall in that slot —
                    a hall you never retrieved is indistinguishable from a hall
                    that does not serve paneer.
"""

from __future__ import annotations

import argparse

from rag.index import Index

# D8 acceptance criteria, verbatim questions + gold halls.
# `slot` is set where a complete answer requires the whole (day, meal) slot.
QUESTIONS = [
    {
        "n": 1, "category": "Lookup",
        "q": "What's for dinner at Hall 12 on Wednesday?",
        "gold_halls": ["Hall 12"], "day": "Wednesday", "meal": "Dinner",
        "needs_full_slot": False,
    },
    {
        "n": 2, "category": "Comparison",
        "q": "Is Friday lunch better at Hall 5 or Hall 4 if I want paneer?",
        "gold_halls": ["Hall 5", "Hall 4"], "day": "Friday", "meal": "Lunch",
        "needs_full_slot": False,
    },
    {
        "n": 3, "category": "Aggregation",
        "q": "How many halls serve paneer at dinner on Tuesday?",
        "gold_halls": ["GH 1", "Hall 2", "Hall 5", "Hall 6", "Hall 9",
                       "Hall 10", "Hall 13", "Hall 14"],
        "day": "Tuesday", "meal": "Dinner", "needs_full_slot": True,
    },
    {
        "n": 4, "category": "Negation",
        "q": "Which halls do NOT serve chicken at dinner on Tuesday?",
        "gold_halls": ["Hall 2", "Hall 3", "Hall 4", "Hall 5", "Hall 6",
                       "Hall 7", "Hall 9", "Hall 11", "Hall 13"],
        "day": "Tuesday", "meal": "Dinner", "needs_full_slot": True,
    },
    {
        "n": 5, "category": "Temporal",
        "q": "Which mess has chicken for dinner tonight?",
        "gold_halls": ["GH 1", "Hall 8", "Hall 10", "Hall 12", "Hall 14"],
        "day": "Tuesday", "meal": "Dinner", "needs_full_slot": True,
        "note": "asked on a Tuesday; 'tonight' must resolve to Tuesday Dinner",
    },
    {
        "n": 6, "category": "Fuzzy semantic",
        "q": "Which halls do a South-Indian style breakfast on Sunday?",
        "gold_halls": ["Hall 2", "Hall 3", "Hall 5", "Hall 10", "Hall 12",
                       "Hall 13", "Hall 14"],
        "day": "Sunday", "meal": "Breakfast", "needs_full_slot": True,
    },
    {
        "n": 7, "category": "Policy",
        "q": "Which halls are girls' halls, and do they serve non-veg?",
        "gold_halls": ["GH 1", "Hall 4"], "day": None, "meal": None,
        "needs_full_slot": False,
        "note": "hall metadata, not menu; any chunk for those halls carries it",
    },
]


def slug(hall: str) -> str:
    return hall.lower().replace(" ", "-")


def gold_chunk_ids(spec) -> set[str]:
    if spec["day"] is None:
        return set()   # policy: any chunk of that hall will do, handled separately
    return {f"{slug(h)}__{spec['day'].lower()}__{spec['meal'].lower()}"
            for h in spec["gold_halls"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--sweep", type=int, nargs="*", default=[5, 10, 20, 40],
                    help="also report recall at these k values")
    args = ap.parse_args(argv)

    ix = Index.load()
    all_ids = {c.chunk_id for c in ix.chunks}
    kmax = max(args.sweep + [args.k])

    print("=" * 74)
    print("RETRIEVAL RECALL — D8 acceptance questions, ZERO LLM calls")
    print("=" * 74)

    summary = []
    for spec in QUESTIONS:
        hits = ix.search(spec["q"], k=kmax)
        ranked = [h.chunk.chunk_id for h in hits]
        gold = gold_chunk_ids(spec)

        print(f"\n{'─'*74}\nQ{spec['n']}  {spec['category']}")
        print(f'  "{spec["q"]}"')
        if spec.get("note"):
            print(f"  note: {spec['note']}")

        if spec["day"] is None:
            # Policy: does any retrieved chunk belong to a gold hall?
            halls_seen = [h.chunk.hall for h in hits[:args.k]]
            found = [h for h in spec["gold_halls"] if h in halls_seen]
            print(f"\n  gold halls: {spec['gold_halls']}")
            print(f"  halls in top-{args.k}: {halls_seen}")
            print(f"  gold halls present: {found or 'NONE'}  "
                  f"({len(found)}/{len(spec['gold_halls'])})")
            summary.append((spec, len(found), len(spec["gold_halls"]), None))
        else:
            missing_from_corpus = gold - all_ids
            if missing_from_corpus:
                print(f"  !! gold chunks absent from corpus: {missing_from_corpus}")

            print(f"\n  top-{args.k} retrieved:")
            for h in hits[:args.k]:
                mark = "GOLD" if h.chunk.chunk_id in gold else "    "
                print(f"    {mark} {h.rank:>2}. {h.score:.3f}  {h.chunk.chunk_id}")

            got = [c for c in ranked[:args.k] if c in gold]
            print(f"\n  gold chunks needed: {len(gold)}")
            print(f"  gold retrieved @{args.k}: {len(got)}/{len(gold)} "
                  f"= {100*len(got)/len(gold):.0f}%")

            # rank of each gold chunk
            ranks = {c: (ranked.index(c) + 1 if c in ranked else None) for c in sorted(gold)}
            inside = {c: r for c, r in ranks.items() if r and r <= args.k}
            outside = {c: r for c, r in ranks.items() if not r or r > args.k}
            if outside:
                print(f"  gold MISSED at k={args.k}:")
                for c, r in sorted(outside.items(), key=lambda x: (x[1] or 9999)):
                    print(f"      {c}   (true rank {r if r else f'>{kmax}'})")

            # recall sweep
            sweep = []
            for k in args.sweep:
                n = len([c for c in ranked[:k] if c in gold])
                sweep.append(f"@{k}:{n}/{len(gold)}")
            print(f"  recall sweep: {'  '.join(sweep)}")

            # slot coverage
            cov = None
            if spec["needs_full_slot"]:
                slot_ids = {c.chunk_id for c in ix.chunks
                            if c.day == spec["day"] and c.meal == spec["meal"]}
                seen = len([c for c in ranked[:args.k] if c in slot_ids])
                cov = (seen, len(slot_ids))
                print(f"  SLOT COVERAGE @{args.k}: {seen}/{len(slot_ids)} halls in "
                      f"{spec['day']} {spec['meal']}")
                if seen < len(slot_ids):
                    print(f"      -> {len(slot_ids)-seen} halls never seen. A complete "
                          f"answer is impossible at k={args.k}.")
            summary.append((spec, len(got), len(gold), cov))

    # ------------------------------------------------------------------ summary
    print(f"\n{'='*74}\nSUMMARY at k={args.k}\n{'='*74}")
    print(f"  {'#':<3}{'category':<16}{'gold hit':>10}{'recall':>9}   slot coverage")
    for spec, got, need, cov in summary:
        covs = f"{cov[0]}/{cov[1]} halls" if cov else "n/a"
        print(f"  {spec['n']:<3}{spec['category']:<16}{f'{got}/{need}':>10}"
              f"{100*got/need:>8.0f}%   {covs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
