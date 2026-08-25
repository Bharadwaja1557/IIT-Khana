"""RULING 3: replace the two lookup questions hit by the Phase 1 splitter bug.

Surgical on purpose. Re-running the whole selector would reshuffle every
category (the RNG is consumed sequentially), invalidating the 48 questions
already reviewed and approved. This swaps exactly two and leaves the rest
byte-identical.

The replacements come from the clean candidate pool — slots with no unbalanced
brackets — and reuse the ids `test-lookup-03` / `test-lookup-06` so the set keeps
its shape. The parser itself is NOT fixed: see DECISIONS.md D30.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

OUT = Path("eval/questions")
SEED = 20260825
REPLACE = ["test-lookup-03", "test-lookup-06"]


def is_clean(q) -> bool:
    return all(i.count("(") == i.count(")") and i.count("[") == i.count("]")
               for i in q["gold"]["items"])


def main():
    test = json.loads((OUT / "test.json").read_text())
    cands = json.loads((OUT / "candidates.json").read_text())["candidates"]

    used_slots = {(q["slot"].get("hall"), q["slot"]["day"], q["slot"]["meal"])
                  for q in test["questions"] if q["category"] == "lookup"}
    pool = [c for c in cands
            if c["category"] == "lookup" and is_clean(c)
            and (c["slot"]["hall"], c["slot"]["day"], c["slot"]["meal"]) not in used_slots]

    rng = random.Random(SEED)
    rng.shuffle(pool)

    # Keep slot diversity: don't reuse a (day, meal) already twice in lookup.
    from collections import Counter
    slot_n = Counter((q["slot"]["day"], q["slot"]["meal"])
                     for q in test["questions"] if q["category"] == "lookup"
                     and q["id"] not in REPLACE)

    # Prefer replacements that RESTORE meal balance. The parse artifacts cluster
    # in dinner slots (they live in `extras`), so naively taking the first clean
    # candidates pushed lookup to 6 breakfast / 1 dinner and produced two nearly
    # identical golds (Poha, Jalebi, Curd twice). Score by how under-represented
    # the meal is, then by gold-set distinctness.
    from collections import Counter as _C
    meal_n = _C(q["slot"]["meal"] for q in test["questions"]
                if q["category"] == "lookup" and q["id"] not in REPLACE)
    existing_sigs = {frozenset(q["gold"]["items"]) for q in test["questions"]
                     if q["category"] == "lookup" and q["id"] not in REPLACE}

    def score(c):
        return (meal_n[c["slot"]["meal"]],                 # rarer meal first
                -len(c["gold"]["items"]))                  # richer slot first

    picks = []
    for c in sorted(pool, key=score):
        if len(picks) >= len(REPLACE):
            break
        s = (c["slot"]["day"], c["slot"]["meal"])
        sig = frozenset(c["gold"]["items"])
        if slot_n[s] >= 2 or sig in existing_sigs:
            continue
        picks.append(c)
        slot_n[s] += 1
        meal_n[c["slot"]["meal"]] += 1
        existing_sigs.add(sig)

    assert len(picks) == len(REPLACE), "not enough clean lookup candidates"

    out, swapped = [], []
    it = iter(picks)
    for q in test["questions"]:
        if q["id"] in REPLACE:
            new = dict(next(it))
            old_id = q["id"]
            new["id"] = old_id
            new["notes"] = ("replaces a slot containing Phase 1 splitter "
                            "artifacts — see DECISIONS.md D30")
            swapped.append((old_id, q, new))
            out.append(new)
        else:
            out.append(q)

    test["questions"] = out
    test["patched"] = {
        "ruling": "RULING 3 — parse-artifact lookup slots replaced",
        "replaced": [s[0] for s in swapped],
        "parser_fixed": False,
        "see": "DECISIONS.md D30",
    }
    (OUT / "test.json").write_text(json.dumps(test, indent=2))

    print("=" * 72)
    print("RULING 3 — lookup replacements (surgical; 48 reviewed questions untouched)")
    print("=" * 72)
    for old_id, old, new in swapped:
        print(f"\n{old_id}")
        print(f"  OUT: {old['slot']['hall']} {old['slot']['day']} {old['slot']['meal']}")
        print(f"       {old['q']}")
        bad = [i for i in old["gold"]["items"]
               if i.count("(") != i.count(")") or i.count("[") != i.count("]")]
        print(f"       artifacts: {bad}")
        print(f"  IN : {new['slot']['hall']} {new['slot']['day']} {new['slot']['meal']}")
        print(f"       {new['q']}")
        print(f"       gold ({new['gold']['n']} items): "
              f"base={new['gold']['base']}")
        print(f"       extras={new['gold']['extras']}")

    # verify
    bad = [(q["id"], i) for q in out if q["gold"]["type"] == "item_set"
           for i in q["gold"]["items"]
           if i.count("(") != i.count(")") or i.count("[") != i.count("]")]
    print(f"\nremaining lookup gold items with unbalanced brackets: {len(bad)}")
    assert not bad
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
