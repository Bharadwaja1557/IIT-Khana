"""Build results.md from the checkpointed run.

The table is FLAT — numbers only, no interpretation. Interpretation goes in a
separate, explicitly-marked section, per the Phase 3 brief.

Throttle disclosure (D34/D35): any call whose generation time exceeds
THROTTLE_MS is flagged as suspected provider-side throttling. Flagged calls are
COUNTED and DISCLOSED but remain INCLUDED in p50 — dropping an inconvenient
outlier without saying so is exactly the quiet tuning the integrity rules forbid.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path

RUNS = Path("eval/runs")
QDIR = Path("eval/questions")
OUT = Path("results.md")

CATS = ["lookup", "comparison", "aggregation", "negation", "temporal",
        "fuzzy_semantic"]
SYSTEMS = ["naive_rag", "long_context"]

# Disclosed threshold. Normal calls in this run are single-digit seconds; D34
# recorded a 272 s call with retries=0 as the daily quota neared exhaustion.
THROTTLE_MS = 60_000

GATE_VARIANT = {
    "lookup": "substitute-for-D15 — slot must hold >= 3 items (the hall is named "
              "in the question, so there is no hall set to be vacuous about)",
    "comparison": "substitute-for-D15 — the two named halls must DIFFER on the "
                  "predicate, so exactly one is correct",
    "aggregation": "D15 as written — gold names 2..12 of 14 halls",
    "negation": "D15 as written + D14 — gold names 2..12 of 14 halls AND the "
                "predicate must be a meaningful-absence (group-2) item",
    "temporal": "D15 as written — gold names 2..12 of 14 halls",
    "fuzzy_semantic": "D15 applied to the hand label — gold names 2..12 of 14 halls",
}


def load():
    recs = [json.loads(l) for l in (RUNS / "test.jsonl").read_text().splitlines() if l.strip()]
    # last write wins per (system, question)
    by = {}
    for r in recs:
        by[(r["system"], r["question_id"])] = r
    return list(by.values())


def p50(xs):
    return statistics.median(xs) if xs else 0.0


def main() -> int:
    recs = load()
    ok = [r for r in recs if not r.get("error")]
    errs = [r for r in recs if r.get("error")]
    scored = [r for r in ok if "SKIPPED" not in r.get("grade", {})]
    skipped = [r for r in ok if "SKIPPED" in r.get("grade", {})]

    agg = defaultdict(lambda: defaultdict(list))
    for r in scored:
        agg[r["system"]][r["category"]].append(r)

    base = json.loads((QDIR / "baselines.json").read_text())
    L = []
    A = L.append

    A("# IIT Khana — Phase 3 results")
    A("")
    A(f"Model: **`{ok[0]['model'] if ok else '?'}`** (all systems, one string — D26/D35).  ")
    A(f"Questions scored: **{len(scored)//max(len(SYSTEMS),1)}** per system. "
      f"Errors: **{len(errs)}**. Unscored (unlabelled): "
      f"**{len(skipped)//max(len(SYSTEMS),1)}**.")
    A("")
    A("No interpretation in the tables below. See *My read* at the end.")
    A("")

    # ---------------------------------------------------------- accuracy
    A("## Accuracy")
    A("")
    A("| category | n | naive RAG exact | naive RAG F1 | long-context exact | long-context F1 |")
    A("|---|---:|---:|---:|---:|---:|")
    for c in CATS:
        rows = agg["naive_rag"].get(c, [])
        n = len(rows)
        if not n:
            continue
        cells = []
        for s in SYSTEMS:
            rs = agg[s].get(c, [])
            em = 100 * sum(r["grade"]["exact_match"] for r in rs) / len(rs) if rs else 0
            f1 = sum(r["grade"]["f1"] for r in rs) / len(rs) if rs else 0
            cells += [f"{em:.0f}%", f"{f1:.2f}"]
        A(f"| {c} | {n} | {cells[0]} | {cells[1]} | {cells[2]} | {cells[3]} |")
    # overall
    cells = []
    for s in SYSTEMS:
        rs = [r for c in CATS for r in agg[s].get(c, [])]
        em = 100 * sum(r["grade"]["exact_match"] for r in rs) / len(rs) if rs else 0
        f1 = sum(r["grade"]["f1"] for r in rs) / len(rs) if rs else 0
        cells += [f"{em:.0f}%", f"{f1:.2f}"]
    n_tot = len([r for c in CATS for r in agg["naive_rag"].get(c, [])])
    A(f"| **OVERALL** | **{n_tot}** | **{cells[0]}** | **{cells[1]}** | "
      f"**{cells[2]}** | **{cells[3]}** |")
    A("")

    # ------------------------------------------------- trivial baselines
    A("## Trivial baselines (no LLM, no retrieval)")
    A("")
    A("| baseline | category | exact | F1 |")
    A("|---|---|---:|---:|")
    for name in ("name-all-14", "name-none", "random-2way"):
        for c in CATS:
            if c not in base.get(name, {}):
                continue
            em, f1, _n = base[name][c]
            A(f"| {name} | {c} | {100*em:.0f}% | {f1:.2f} |")
    A("")

    # ------------------------------------------------------------ cost
    A("## Tokens per query")
    A("")
    A("| system | category | prompt | cached | completion | thinking |")
    A("|---|---|---:|---:|---:|---:|")
    for s in SYSTEMS:
        for c in CATS:
            rs = agg[s].get(c, [])
            if not rs:
                continue
            A(f"| {s} | {c} | {statistics.mean(r['prompt_tokens'] for r in rs):.0f} "
              f"| {statistics.mean(r['cached_tokens'] for r in rs):.0f} "
              f"| {statistics.mean(r['completion_tokens'] for r in rs):.0f} "
              f"| {statistics.mean(r['thinking_tokens'] for r in rs):.0f} |")
    A("")

    # --------------------------------------------------------- latency
    thr = [r for r in ok if r["generation_ms"] > THROTTLE_MS]
    A("## p50 latency (ms)")
    A("")
    A("| system | category | p50 total | p50 generation | p50 retrieval |")
    A("|---|---|---:|---:|---:|")
    for s in SYSTEMS:
        for c in CATS:
            rs = agg[s].get(c, [])
            if not rs:
                continue
            A(f"| {s} | {c} | {p50([r['total_ms'] for r in rs]):.0f} "
              f"| {p50([r['generation_ms'] for r in rs]):.0f} "
              f"| {p50([r['retrieval_ms'] for r in rs]):.0f} |")
    A("")

    # -------------------------------- precision when answering (CHECK 2)
    A("## Precision when answering (non-abstained questions only)")
    A("")
    A("Additional column, **not** a replacement: the headline Accuracy table "
      "above scores abstentions as incorrect. The two systems have very "
      "different answer policies, so without this the comparison is partly "
      "measuring willingness to answer rather than architecture.")
    A("")
    A("| system | category | all (headline) | when answering | n answered | abstained |")
    A("|---|---|---:|---:|---:|---:|")
    for s in SYSTEMS:
        for c in CATS:
            rs = agg[s].get(c, [])
            if not rs:
                continue
            ans = [r for r in rs if not r["grade"].get("abstained")]
            em_all = 100 * sum(r["grade"]["exact_match"] for r in rs) / len(rs)
            cell = ("n/a" if not ans else
                    f"{100*sum(r['grade']['exact_match'] for r in ans)/len(ans):.0f}%")
            A(f"| {s} | {c} | {em_all:.0f}% | {cell} | {len(ans)} "
              f"| {len(rs)-len(ans)} |")
    A("")

    # ------------------------------------------------------ abstention
    A("## Abstention rate (scored as incorrect, reported separately)")
    A("")
    A("| system | category | abstained | no ANSWER line |")
    A("|---|---|---:|---:|")
    for s in SYSTEMS:
        for c in CATS:
            rs = agg[s].get(c, [])
            if not rs:
                continue
            ab = sum(1 for r in rs if r["grade"].get("abstained"))
            nl = sum(1 for r in rs if not r["grade"].get("had_answer_line"))
            A(f"| {s} | {c} | {ab}/{len(rs)} | {nl}/{len(rs)} |")
    A("")

    # ------------------------------------------------------- footnotes
    A("## Footnotes")
    A("")
    A("**Gate variant per category** (D15 and its substitutes):")
    A("")
    for c in CATS:
        A(f"- `{c}` — {GATE_VARIANT[c]}")
    A("")
    A(f"**Latency** — measured in a single sitting that did not approach the "
      f"quota cap (D35). Calls exceeding **{THROTTLE_MS/1000:.0f} s** generation "
      f"time are flagged as suspected provider-side throttling: "
      f"**{len(thr)} of {len(ok)} calls**. Flagged calls are **included** in p50, "
      f"not dropped.")
    if thr:
        for r in thr:
            A(f"  - `{r['system']}` `{r['question_id']}` — "
              f"{r['generation_ms']/1000:.0f} s, retries={r['retries']}")
    A("")
    A("**Cached tokens** — read from `usage_metadata.cached_content_token_count` "
      "per call. Gemini caches repeated prefixes **implicitly**, with no cache "
      "object and no opt-out (D33), so no column here can be labelled "
      "\"uncached\"; the figures are what was observed.")
    A("")
    A("**Abstentions** are scored **incorrect**, never excluded (same accounting "
      "as the Phase 1 tagger).")
    A("")
    if errs:
        A(f"**Errors** — {len(errs)} call(s) failed and are excluded from "
          f"accuracy, listed here rather than silently dropped:")
        for r in errs:
            A(f"  - `{r['system']}` `{r['question_id']}`: {r['error'][:120]}")
        A("")
    if skipped:
        ids = sorted({r["question_id"] for r in skipped})
        A(f"**Unscored** — {len(ids)} question(s) carry no hand label and are "
          f"excluded from every figure: {', '.join(f'`{i}`' for i in ids)}.")
        A("")

    # The hand-written "My read" and side-experiment sections live at the end of
    # results.md and are NOT generated. Preserve them across regeneration —
    # regenerating the tables silently wiped them once already.
    tail = ""
    if OUT.exists():
        prev = OUT.read_text()
        for marker in ("\n---\n\n## Side experiment", "\n---\n\n## My read"):
            if marker in prev:
                tail = prev[prev.index(marker):]
                break

    OUT.write_text("\n".join(L) + "\n" + tail)
    print("\n".join(L))
    if tail:
        print(f"\n[preserved {len(tail)} chars of hand-written sections]")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
