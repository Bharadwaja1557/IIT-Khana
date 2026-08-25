"""Run the systems over a question set, checkpointing after every question.

Free-tier daily caps will bite mid-run, so every result is appended to a JSONL
checkpoint immediately. Re-running skips (system, question_id) pairs already
present, so an interrupted run RESUMES rather than restarts.

Integrity: this script only runs and records. It never edits a question, never
retries a "bad-looking" answer, and never re-scores. If something looks broken,
the run finishes and the breakage is reported.
"""

from __future__ import annotations

import argparse
import json
import time
import traceback
from datetime import date
from pathlib import Path

from eval.grade import grade
from rag.systems import LongContext, NaiveRAG

QDIR = Path("eval/questions")
RUNS = Path("eval/runs")


def load_questions(which: str) -> list[dict]:
    qs = list(json.loads((QDIR / "test.json").read_text())["questions"])

    # Fuzzy gold comes from the HAND LABELS, joined in here. Questions whose
    # label was left null are carried with gold=None and skipped at scoring —
    # never labelled by this code.
    lab = {q["id"].replace("label-", ""): q
           for q in json.loads((QDIR / "fuzzy_for_label.json").read_text())["questions"]}
    cand = {q["id"].replace("cand-", ""): q
            for q in json.loads((QDIR / "fuzzy_candidates.json").read_text())["questions"]}
    for key in sorted(lab):
        src, l = cand[key], lab[key]
        qs.append({
            "id": f"test-fuzzy_semantic-{key.split('-')[1]}",
            "category": "fuzzy_semantic", "q": src["q"], "as_of": src["as_of"],
            "slot": src["slot"], "predicate": {"kind": "semantic",
                                               "value": src["predicate"]["value"]},
            "gold": ({"type": "hall_set", "halls": l["label_halls"],
                      "n": len(l["label_halls"])} if l["label_halls"] is not None
                     else {"type": "hall_set", "halls": None, "n": None,
                           "STATUS": "UNLABELLED — not scored"}),
            "sql": "N/A — hand-labelled (predicate absent from corpus)",
            "sql_params": [],
            "gate": {"rule": "D15 on hand label"},
        })

    if which == "dev":
        return json.loads((QDIR / "dev.json").read_text())["questions"]
    return qs


def load_done(path: Path) -> set[tuple[str, str]]:
    if not path.exists():
        return set()
    done = set()
    for line in path.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            done.add((r["system"], r["question_id"]))
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="test", choices=["test", "dev"])
    ap.add_argument("--systems", nargs="*", default=["naive_rag", "long_context"])
    ap.add_argument("--out", default=None)
    ap.add_argument("--sleep", type=float, default=3.0)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)

    RUNS.mkdir(parents=True, exist_ok=True)
    out = Path(args.out or RUNS / f"{args.set}.jsonl")
    done = load_done(out)
    if done:
        print(f"resuming — {len(done)} (system, question) pairs already recorded")

    questions = load_questions(args.set)
    if args.limit:
        questions = questions[:args.limit]

    built = {}
    if "naive_rag" in args.systems:
        built["naive_rag"] = NaiveRAG()
    if "long_context" in args.systems:
        built["long_context"] = LongContext()

    todo = [(s, q) for s in args.systems for q in questions
            if (s, q["id"]) not in done]
    print(f"{len(questions)} questions x {len(args.systems)} systems "
          f"= {len(questions)*len(args.systems)}; {len(todo)} to run\n")

    for i, (sysname, q) in enumerate(todo, 1):
        as_of = date.fromisoformat(q.get("as_of", "2026-08-25"))
        rec = {"system": sysname, "question_id": q["id"],
               "category": q["category"], "q": q["q"], "as_of": q["as_of"]}
        try:
            a = built[sysname].query(q["q"], as_of)
            rec.update({
                "answer": a.answer, "resolved_query": a.resolved_query,
                "context_line": a.context_line,
                "retrieved_chunk_ids": a.retrieved_chunk_ids,
                "citations": [c.__dict__ for c in a.citations],
                "dropped_citations": a.dropped_citations,
                "k": a.k, "model": a.model,
                "prompt_tokens": a.prompt_tokens,
                "completion_tokens": a.completion_tokens,
                "thinking_tokens": a.thinking_tokens,
                "cached_tokens": a.cached_tokens,
                "retries": a.retries,
                "retrieval_ms": a.retrieval_ms,
                "generation_ms": a.generation_ms,
                "total_ms": a.total_ms,
                "error": None,
            })
            gold = q.get("gold")
            if gold is None:
                # DEV questions carry no gold — the set exists to debug the
                # harness, never to be scored (B1).
                rec["grade"] = {"SKIPPED": "dev set has no gold"}
            elif gold.get("halls") is not None or gold.get("type") == "item_set":
                rec["grade"] = grade(q, a.answer)
            else:
                rec["grade"] = {"SKIPPED": "question unlabelled by hand"}
        except Exception as e:                              # noqa: BLE001
            rec.update({"error": f"{type(e).__name__}: {e}",
                        "traceback": traceback.format_exc()[-800:]})
            print(f"  [{i}/{len(todo)}] {sysname} {q['id']}  ERROR: {rec['error']}")

        # checkpoint IMMEDIATELY
        with out.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")

        if not rec.get("error"):
            g = rec.get("grade", {})
            mark = ("--" if "SKIPPED" in g else
                    ("OK " if g.get("exact_match") else "XX "))
            print(f"  [{i}/{len(todo)}] {mark} {sysname:<13} {q['id']:<26} "
                  f"f1={g.get('f1', 0):.2f} "
                  f"prompt={rec['prompt_tokens']} cached={rec['cached_tokens']} "
                  f"{rec['total_ms']:.0f}ms")
        if i < len(todo):
            time.sleep(args.sleep)

    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
