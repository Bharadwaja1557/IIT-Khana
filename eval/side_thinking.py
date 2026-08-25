"""SIDE EXPERIMENT — NOT a results-table row.

Tests one objection to the Phase 3 read: long-context lost exactly the
categories needing multi-step work (aggregation, temporal, negation), and the
table's model — gemini-3.5-flash-lite — emits ZERO thinking tokens, where
Phase 2 measured 582/query on gemini-3.6-flash.

So "having every fact in the prompt is not the same as being able to use it"
may really be "a non-reasoning model cannot aggregate 294 rows in one forward
pass". This runs long-context over the 10 AGGREGATION questions on
gemini-3.6-flash (thinking at model default) and reports against the same gold.

DIFFERENT MODEL. Explicitly excluded from the main table (D26: one model string
per table). Written to its own file; the main run is never touched.
"""
from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from eval.grade import grade
from eval.run import load_questions
from rag.llm import GeminiLLM
from rag.systems import LongContext

OUT = Path("eval/runs/side_thinking.jsonl")
MODEL = "gemini-3.6-flash"


def main() -> int:
    load_dotenv()
    qs = [q for q in load_questions("test") if q["category"] == "aggregation"]
    done = set()
    if OUT.exists():
        done = {json.loads(l)["question_id"]
                for l in OUT.read_text().splitlines() if l.strip()}
    lc = LongContext(llm=GeminiLLM(model=MODEL))
    todo = [q for q in qs if q["id"] not in done]
    print(f"SIDE EXPERIMENT: long_context on {MODEL}, {len(todo)} aggregation questions\n")
    for i, q in enumerate(todo, 1):
        rec = {"experiment": "thinking_vs_no_thinking", "system": "long_context",
               "model": MODEL, "question_id": q["id"], "category": q["category"],
               "q": q["q"]}
        try:
            a = lc.query(q["q"], date.fromisoformat(q["as_of"]))
            rec.update({"answer": a.answer, "grade": grade(q, a.answer),
                        "prompt_tokens": a.prompt_tokens,
                        "completion_tokens": a.completion_tokens,
                        "thinking_tokens": a.thinking_tokens,
                        "cached_tokens": a.cached_tokens,
                        "generation_ms": a.generation_ms,
                        "retries": a.retries, "error": None})
            g = rec["grade"]
            print(f"  [{i}/{len(todo)}] {'OK ' if g['exact_match'] else 'XX '} "
                  f"{q['id']:<26} f1={g['f1']:.2f} think={a.thinking_tokens} "
                  f"{a.generation_ms:.0f}ms")
        except Exception as e:                                  # noqa: BLE001
            rec.update({"error": f"{type(e).__name__}: {str(e)[:200]}"})
            print(f"  [{i}/{len(todo)}] ERROR {q['id']}: {rec['error'][:120]}")
        with OUT.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
        if i < len(todo):
            time.sleep(3)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
