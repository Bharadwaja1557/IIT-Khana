"""Grading. Deterministic where it can be, judged where it cannot.

Structured categories (lookup, comparison, aggregation, negation, temporal) are
graded by EXACT SET MATCH against SQL gold, with set F1 reported alongside. The
system's claim is read from the mandatory `ANSWER:` line, so grading never has to
parse prose — a regex over free text would misread negation answers, where a
system legitimately mentions halls it is excluding.

Abstentions are scored INCORRECT, not excluded, and the abstention rate is
reported separately — the same accounting used for the Phase 1 tagger.
"""

from __future__ import annotations

import re
import unicodedata

HALLS = ["GH 1"] + [f"Hall {i}" for i in range(2, 15)]

ANSWER_RE = re.compile(r"^\s*ANSWER\s*:\s*(.*)$", re.I | re.M)

# Phrases that mean "I could not answer from what I was given". Scored wrong,
# counted separately.
ABSTAIN_RE = re.compile(
    r"do(?: not|es not|n't)\s+(?:contain|include|cover|show)|"
    r"not\s+(?:enough|sufficient)\s+information|"
    r"cannot\s+(?:determine|tell|answer)|"
    r"no\s+excerpts?\s+(?:for|cover)|"
    r"only\s+(?:cover|show|include)s?\s+", re.I)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = s.lower().replace("-", " ").replace("_", " ")
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def extract_answer_line(text: str) -> str | None:
    m = list(ANSWER_RE.finditer(text or ""))
    return m[-1].group(1).strip() if m else None


def parse_halls(text: str) -> tuple[set[str], bool]:
    """-> (hall set, had_answer_line). Reads the ANSWER: line only."""
    line = extract_answer_line(text)
    if line is None:
        return set(), False
    if norm(line) in {"none", "no halls", "nothing", "n a", "na", ""}:
        return set(), True
    found = set()
    nl = norm(line)
    for h in HALLS:
        # word-boundary match on the normalised form; "hall 1" must not match
        # "hall 11", so require a non-digit (or end) after the number.
        if re.search(rf"\b{re.escape(norm(h))}(?!\d)", nl):
            found.add(h)
    return found, True


def parse_items(text: str) -> tuple[list[str], bool]:
    line = extract_answer_line(text)
    if line is None:
        return [], False
    if norm(line) in {"none", "nothing", ""}:
        return [], True
    return [p.strip() for p in line.split(",") if p.strip()], True


def f1(pred: set, gold: set) -> float:
    if not pred and not gold:
        return 1.0
    if not pred or not gold:
        return 0.0
    tp = len(pred & gold)
    if not tp:
        return 0.0
    p, r = tp / len(pred), tp / len(gold)
    return 2 * p * r / (p + r)


def item_match(claimed: list[str], gold_items: list[str],
               distractors: list[str]) -> tuple[float, float, dict]:
    """Lookup grading.

    A gold item counts as found if its normalised form appears in the claim.
    A false positive is a claimed item matching a DISTRACTOR — an item served in
    the same (day, meal) at a DIFFERENT hall. Distractors are computed from the
    corpus, so both systems are judged against exactly the same false-positive
    set; neither can be advantaged by what it happened to retrieve.
    """
    cl = [norm(c) for c in claimed]

    def present(x):
        """Is gold item `x` covered by something the system claimed?"""
        nx = norm(x)
        return any(nx in c or c in nx for c in cl)

    hit = [g for g in gold_items if present(g)]
    gold_norm = {norm(g) for g in gold_items}
    dist_norm = [(d, norm(d)) for d in distractors if norm(d) not in gold_norm]

    # FALSE POSITIVES ARE PER CLAIMED ITEM, NOT PER DISTRACTOR.
    #
    # The original version iterated distractors and asked "does any claimed
    # string contain this distractor?" — which is backwards, and made substring
    # collisions inevitable: claiming "Papad" was scored as also claiming the
    # distractor "Roasted Papad"; "Coconut Chutney" as also claiming "Chutney";
    # "Arhar Dal" as also claiming "Kachodi Arhar Dal". Every lookup answer
    # therefore picked up phantom false positives and exact match was 0% for
    # BOTH systems for reasons unrelated to either system.
    #
    # Correct rule: a CLAIMED item is a false positive only if it matches no
    # gold item at all AND does match a distractor.
    def matches_gold(c):
        return any(c in g or g in c for g in gold_norm)

    fp = []
    for raw, c in zip(claimed, cl):
        if matches_gold(c):
            continue
        for d, nd in dist_norm:
            if c in nd or nd in c:
                fp.append(d)
                break

    recall = len(hit) / len(gold_items) if gold_items else 1.0
    prec = 1 - (len(fp) / max(len(cl), 1))
    prec = max(0.0, min(1.0, prec))
    score = 0.0 if (prec + recall) == 0 else 2 * prec * recall / (prec + recall)
    exact = 1.0 if (len(hit) == len(gold_items) and not fp) else 0.0
    return exact, score, {"found": hit, "missed": [g for g in gold_items
                                                   if g not in hit],
                          "false_positives": fp}


def grade(question: dict, answer_text: str) -> dict:
    cat = question["category"]
    gold = question["gold"]
    abstained = bool(ABSTAIN_RE.search(answer_text or ""))

    if gold["type"] == "item_set":
        claimed, had_line = parse_items(answer_text)
        exact, score, detail = item_match(
            claimed, gold["items"], gold.get("distractors", []))
        return {"category": cat, "exact_match": exact, "f1": score,
                "abstained": abstained, "had_answer_line": had_line,
                "claimed": claimed, "gold": gold["items"], "detail": detail}

    pred, had_line = parse_halls(answer_text)
    g = set(gold["halls"])
    return {"category": cat,
            "exact_match": 1.0 if pred == g else 0.0,
            "f1": f1(pred, g),
            "abstained": abstained, "had_answer_line": had_line,
            "claimed": sorted(pred), "gold": sorted(g),
            "detail": {"missed": sorted(g - pred),
                       "false_positives": sorted(pred - g)}}
