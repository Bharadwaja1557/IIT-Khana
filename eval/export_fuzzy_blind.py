"""ADDITION 1: blind fuzzy labelling export.

Emits eval/questions/fuzzy_for_label.json containing, per question, the question
and every hall's raw slot items — and NOTHING else.

Deliberately ABSENT, so the label is formed from menu contents alone:
  * no candidate_gold          * no keyword evidence
  * no heuristic keyword list  * no match markers, counts, or ordering hints

Halls are listed alphabetically (a neutral order, not a ranked one) and every
hall in the slot appears, including those with no plausible match — otherwise
mere presence in the list would leak the proposal.

The machine candidates stay in eval/questions/fuzzy_candidates.json, which is
not to be opened until after labelling. Agreement between the two is then a
measurement, not a formality: near-total agreement would mean the fuzzy category
is LEXICAL rather than semantic, which would undercut D10's justification for
keeping a retrieval path at all. That is a finding either way.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB = Path("db/khana.db")
OUT = Path("eval/questions")


def main() -> int:
    conn = sqlite3.connect(DB)
    src = json.loads((OUT / "fuzzy_candidates.json").read_text())

    out = []
    for q in src["questions"]:
        day, meal = q["slot"]["day"], q["slot"]["meal"]
        rows = conn.execute("""
            SELECT h.name, m.item_raw, m.is_extra
              FROM menu_item m JOIN hall h ON h.id = m.hall_id
             WHERE m.day_of_week = ? AND m.meal = ?
             ORDER BY h.name, m.is_extra, m.position""", (day, meal)).fetchall()

        halls: dict[str, dict] = {}
        for hall, item, is_extra in rows:
            e = halls.setdefault(hall, {"menu": [], "extras": []})
            e["extras" if is_extra else "menu"].append(item)

        out.append({
            "id": q["id"].replace("cand-", "label-"),
            "category": "fuzzy_semantic",
            "question": q["q"],
            "slot": {"day": day, "meal": meal},
            "as_of": q["as_of"],
            "instructions": ("Read each hall's items and decide whether that "
                             "hall satisfies the question. Put the hall names "
                             "that DO into `label_halls`. Leave `label_halls` "
                             "as null if you want to skip."),
            "halls": halls,            # every hall in the slot, alphabetical
            "label_halls": None,       # <- you fill this in
        })

    path = OUT / "fuzzy_for_label.json"
    path.write_text(json.dumps(
        {"set": "fuzzy_for_label", "scored": False,
         "note": ("Blind labelling set. Contains menu contents only — no "
                  "machine proposal, no keywords, no evidence markers."),
         "n": len(out), "questions": out}, indent=2))

    # Guard: assert the QUESTIONS payload leaks nothing from the candidate file.
    # Scoped to `questions` on purpose — the top-level `note` legitimately says
    # "no evidence markers", and scanning it would flag the disclaimer itself.
    blob = json.dumps(out).lower()
    leaks = [k for k in ("candidate_gold", "heuristic", "matched", "evidence",
                         "warning", "keyword") if k in blob]
    assert not leaks, f"blind file leaks: {leaks}"

    # And assert the payload keys are exactly the intended ones.
    allowed = {"id", "category", "question", "slot", "as_of", "instructions",
               "halls", "label_halls"}
    for q in out:
        extra = set(q) - allowed
        assert not extra, f"unexpected keys in blind payload: {extra}"

    print(f"wrote {path}  ({len(out)} questions, label_halls=null)")
    print("  leak check: clean — no candidate_gold / heuristic / matched / evidence")
    print()
    for q in out:
        n_items = sum(len(v["menu"]) + len(v["extras"]) for v in q["halls"].values())
        print(f"  {q['id']:<16} {q['slot']['day'][:3]}/{q['slot']['meal'][:3]}  "
              f"{len(q['halls']):>2} halls, {n_items:>3} items   {q['question']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
