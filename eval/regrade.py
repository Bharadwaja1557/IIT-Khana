"""Re-grade stored answers in place. NO new API calls — answers are unchanged.

Used once, to correct the lookup false-positive defect. Per the Phase 3
integrity protocol, before and after numbers are both reported.
"""
from __future__ import annotations

import json
from pathlib import Path

from eval.grade import grade
from eval.run import load_questions

RUNS = Path("eval/runs/test.jsonl")


def main() -> int:
    qs = {q["id"]: q for q in load_questions("test")}
    out = []
    for line in RUNS.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        q = qs.get(r["question_id"])
        if q and not r.get("error") and "SKIPPED" not in r.get("grade", {}):
            r["grade"] = grade(q, r["answer"])
        out.append(r)
    RUNS.write_text("\n".join(json.dumps(r) for r in out) + "\n")
    print(f"re-graded {len(out)} records from stored answers (0 API calls)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
