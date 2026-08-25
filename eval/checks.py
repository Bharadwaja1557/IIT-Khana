"""CHECK 2 and CHECK 3 — re-analysis of stored records. No API calls.

CHECK 2  precision-when-answering: accuracy over NON-ABSTAINED questions only.
         Without it the comparison partly measures answer POLICY rather than
         architecture — one system declining to answer is not the same failure
         as another answering wrongly.

CHECK 3  correctness vs gold set size for aggregation and negation. Phase 2
         found full slot coverage needs k=76, yet naive RAG scores F1 0.90 on
         aggregation at k=5. That reconciles only if aggregation golds skew
         small. If accuracy collapses above gold n~5, the result is a property
         of the D15 band, not of the architecture.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path

RUNS = Path("eval/runs/test.jsonl")
QDIR = Path("eval/questions")
CATS = ["lookup", "comparison", "aggregation", "negation", "temporal",
        "fuzzy_semantic"]
SYSTEMS = ["naive_rag", "long_context"]


def load():
    by = {}
    for l in RUNS.read_text().splitlines():
        if not l.strip():
            continue
        r = json.loads(l)
        if r.get("error") or "SKIPPED" in r.get("grade", {}):
            continue
        by[(r["system"], r["question_id"])] = r
    return list(by.values())


def check2(recs):
    print("=" * 78)
    print("CHECK 2 — precision when answering (non-abstained only)")
    print("=" * 78)
    print(f"  {'system':<14}{'category':<16}{'all':>10}{'answered':>10}"
          f"{'n_ans':>7}{'abstain':>9}")
    out = {}
    for s in SYSTEMS:
        for c in CATS:
            rs = [r for r in recs if r["system"] == s and r["category"] == c]
            if not rs:
                continue
            ans = [r for r in rs if not r["grade"].get("abstained")]
            em_all = 100 * sum(r["grade"]["exact_match"] for r in rs) / len(rs)
            em_ans = (100 * sum(r["grade"]["exact_match"] for r in ans) / len(ans)
                      if ans else float("nan"))
            out[(s, c)] = (em_all, em_ans, len(ans), len(rs) - len(ans))
            shown = "  n/a" if not ans else f"{em_ans:.0f}%"
            print(f"  {s:<14}{c:<16}{em_all:>9.0f}%{shown:>10}"
                  f"{len(ans):>7}{len(rs)-len(ans):>9}")
        print()
    return out


def check3(recs):
    qs = {}
    for q in json.loads((QDIR / "test.json").read_text())["questions"]:
        qs[q["id"]] = q
    print("=" * 78)
    print("CHECK 3 — correctness vs gold set size (aggregation, negation)")
    print("=" * 78)
    res = {}
    for c in ("aggregation", "negation"):
        print(f"\n  --- {c} ---")
        for s in SYSTEMS:
            rows = []
            for r in recs:
                if r["system"] != s or r["category"] != c:
                    continue
                q = qs.get(r["question_id"])
                if not q:
                    continue
                rows.append((q["gold"]["n"], r["grade"]["exact_match"],
                             r["grade"]["f1"]))
            rows.sort()
            print(f"    {s}")
            print(f"      gold n : {[n for n, _, _ in rows]}")
            print(f"      exact  : {[int(e) for _, e, _ in rows]}")
            print(f"      F1     : {[round(f,2) for _, _, f in rows]}")
            ns = [n for n, _, _ in rows]
            ems = [e for _, e, _ in rows]
            f1s = [f for _, _, f in rows]
            # correlation of gold size with correctness
            try:
                r_em = statistics.correlation(ns, ems)
            except Exception:
                r_em = float("nan")
            try:
                r_f1 = statistics.correlation(ns, f1s)
            except Exception:
                r_f1 = float("nan")
            small = [e for n, e, _ in rows if n <= 4]
            large = [e for n, e, _ in rows if n >= 5]
            print(f"      corr(gold_n, exact) = {r_em:+.2f}   "
                  f"corr(gold_n, F1) = {r_f1:+.2f}")
            print(f"      exact when gold<=4: "
                  f"{sum(small)}/{len(small)}" if small else "      (none <=4)")
            print(f"      exact when gold>=5: "
                  f"{sum(large)}/{len(large)}" if large else "      (none >=5)")
            res[(s, c)] = {"n": ns, "exact": ems, "f1": f1s,
                           "corr_exact": r_em, "corr_f1": r_f1}
    return res


def main() -> int:
    recs = load()
    c2 = check2(recs)
    c3 = check3(recs)
    Path("eval/runs/checks.json").write_text(json.dumps(
        {"check2": {f"{k[0]}|{k[1]}": v for k, v in c2.items()},
         "check3": {f"{k[0]}|{k[1]}": v for k, v in c3.items()}},
        indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
