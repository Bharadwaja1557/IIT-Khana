"""LLM-judge for the fuzzy-semantic category, with reasoning logged for audit.

The brief requires an LLM judge here. Set comparison against the hand labels is
still the PRIMARY metric — it is deterministic and consistent with the other
categories — but a fuzzy answer can be defensible while not matching exactly, so
the judge gives a second opinion that is auditable rather than assumed.

Every judge decision is logged with its stated reasoning. Where judge and set
comparison disagree, both are reported.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from rag.llm import get_llm

RUNS = Path("eval/runs")

SYSTEM = """You are auditing an answer to a question about mess menus.

You are given: the question, the HUMAN GOLD set of halls, and the halls the
system ANSWERED. Decide whether the system's answer is acceptable.

Judge only the hall set. Reply in exactly this format:

VERDICT: correct | partially_correct | incorrect
REASON: <one sentence>

"correct" means the answered set matches the gold set. "partially_correct"
means substantial overlap with a small number of misses or extras.
"incorrect" means the sets differ substantially."""


def main() -> int:
    recs = [json.loads(l) for l in (RUNS / "test.jsonl").read_text().splitlines() if l.strip()]
    fz = [r for r in recs if r["category"] == "fuzzy_semantic"
          and not r.get("error") and "SKIPPED" not in r.get("grade", {})]
    llm = get_llm()
    out = []
    print(f"judging {len(fz)} fuzzy answers on {llm.model}\n")
    for i, r in enumerate(fz, 1):
        g = r["grade"]
        user = (f"Question: {r['q']}\n"
                f"HUMAN GOLD halls: {', '.join(g['gold']) or 'none'}\n"
                f"System ANSWERED halls: {', '.join(g['claimed']) or 'none'}")
        res = llm.complete(SYSTEM, user)
        v = re.search(r"VERDICT:\s*(\w+)", res.text)
        why = re.search(r"REASON:\s*(.*)", res.text)
        rec = {"system": r["system"], "question_id": r["question_id"],
               "q": r["q"], "gold": g["gold"], "claimed": g["claimed"],
               "set_exact": g["exact_match"], "set_f1": round(g["f1"], 3),
               "judge_verdict": (v.group(1).lower() if v else "UNPARSED"),
               "judge_reason": (why.group(1).strip() if why else res.text[:200])}
        out.append(rec)
        print(f"  [{i}/{len(fz)}] {rec['system']:<13} {rec['question_id']:<26} "
              f"set_exact={rec['set_exact']:.0f} judge={rec['judge_verdict']}")
        print(f"      {rec['judge_reason'][:110]}")
        if i < len(fz):
            time.sleep(2)

    (RUNS / "fuzzy_judge.jsonl").write_text(
        "\n".join(json.dumps(r) for r in out) + "\n")

    print("\n  agreement between LLM judge and deterministic set match:")
    for s in ("naive_rag", "long_context"):
        v = [r for r in out if r["system"] == s]
        if not v:
            continue
        agree = sum(1 for r in v
                    if (r["judge_verdict"] == "correct") == bool(r["set_exact"]))
        print(f"    {s:<13} {agree}/{len(v)} agree")
    print(f"\nwrote {RUNS/'fuzzy_judge.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
