"""Ask the naive RAG baseline one question.

    python -m scripts.query "which halls have chicken for dinner on tuesday"
"""
from __future__ import annotations

import argparse
import sys

from rag.pipeline import DEFAULT_K, NaiveRAG


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Query the naive RAG baseline.")
    ap.add_argument("question", nargs="+")
    ap.add_argument("-k", type=int, default=DEFAULT_K, help="top-k chunks")
    ap.add_argument("--provider", default=None, help="override LLM_PROVIDER")
    ap.add_argument("--show-chunks", action="store_true",
                    help="print the retrieved chunk text, not just ids")
    args = ap.parse_args(argv)

    question = " ".join(args.question)

    from rag.llm import get_llm
    try:
        rag = NaiveRAG(llm=get_llm(args.provider), k=args.k)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    try:
        r = rag.query(question)
    except Exception as e:
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print(f"Q: {r.query}\n")
    print(r.answer.strip(), "\n")

    print("Citations:")
    if r.citations:
        for c in r.citations:
            print(f"  [{c.n}] {c.hall} — {c.day} {c.meal}   ({c.chunk_id})")
    else:
        print("  (none)")
    if r.dropped_citations:
        print(f"  dropped (not in prompt): {r.dropped_citations}")

    print("\nRetrieved:")
    for h in r.hits:
        cited = "*" if any(c.n == h.rank for c in r.citations) else " "
        print(f" {cited}[{h.rank}] {h.score:.3f}  {h.chunk.chunk_id}")
        if args.show_chunks:
            for line in h.chunk.text.splitlines():
                print(f"        {line}")

    print(f"\n{r.instrumentation_line()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
