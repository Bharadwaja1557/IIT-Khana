"""Split upstream menu strings into items, and normalise them.

The source ships one comma-joined string per (hall, day, meal) field. Turning
that into items is the single largest source of silent error in this project,
so the rules here are deliberately conservative and each one is justified in
.notes/phase_1.md (Part B.1).
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MEALS = ("Breakfast", "Lunch", "Dinner")

# Separators we split on. Comma and newline only.
#
# " & ", " and ", " with " and "+" were each inspected against real examples and
# rejected: all four are used more often to join parts of ONE dish
# ("Besan & Moong Dal Chilla", "Fried And Roasted Papad", "Aloo+Capsicum+Matar",
# "Sambhar With Coconut Chutney") than to separate two. Splitting on them
# fabricates dishes.
_SPLIT_CHARS = ",\n"

# Alternatives within one item: "Kadhai Paneer / Paneer Butter Masala",
# "Lauki Jeera / Kathal". These are choices offered, so each side is a real item.
_ALT = re.compile(r"\s*/\s*|\s+\bor\b\s+", re.I)

_PARENTHETICAL = re.compile(r"\([^()]*\)")
_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")

# Diet vocabulary for the CLUSTERING GUARD only (D17).
#
# Deliberately restricted to unambiguous protein words. Preparation styles
# (tikka, kebab, lollipop, 65) are excluded because they cut across diets —
# "Paneer Tikka" and "Veg Kabab" are vegetarian.
#
# The Phase 1 C2 tagger gets its own lexicon, built against hand labels and
# measured. This one is not that lexicon and is not evaluated as one.
_NONVEG_MARKERS = (
    "non-veg", "nonveg", "non veg", "chicken", "mutton", "fish", "prawn",
    "katla", "rohu", "keema",
)
_EGG_RE = re.compile(r"\begg\b|\banda\b|omelette|omlette|bhurji")
_VEG_RE = re.compile(r"\bveg\b|\bvegetable\b|\bpaneer\b")


def unwrap_outer_parens(s: str) -> str:
    """Strip parentheses that enclose the ENTIRE string, repeatedly.

    Two fields in the corpus are fully wrapped, one of them nested:
        "(Aloo Baigan Light Gravy, Mix Dal)"
        "(Ajwain Poori / Roti, Matar (Yellow) Sabzi, Halwa)"
    Without this, the depth-aware splitter never sees depth 0 and returns the
    whole field as a single item.
    """
    s = s.strip()
    while len(s) >= 2 and s[0] == "(" and s[-1] == ")":
        depth = 0
        for i, ch in enumerate(s):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                # Closed before the end => the outer parens are not a wrapper.
                if depth == 0 and i != len(s) - 1:
                    return s
        if depth != 0:
            return s  # unbalanced; leave alone
        s = s[1:-1].strip()
    return s


def split_items(field: str | None) -> list[str]:
    """Split one upstream field into raw item strings.

    Splits on comma and newline at bracket depth zero, then expands "/" and "Or"
    alternatives. Returns raw substrings; normalisation is a separate step.
    """
    if not field:
        return []

    s = unwrap_outer_parens(field)

    parts: list[str] = []
    depth = 0
    cur: list[str] = []
    for ch in s:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)  # tolerate stray closers
        if ch in _SPLIT_CHARS and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))

    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        for alt in _ALT.split(p):
            alt = alt.strip(" .-\t")
            if alt:
                out.append(alt)
    return out


def normalize(item: str) -> str:
    """Lowercase, drop parentheticals, strip punctuation, collapse whitespace."""
    s = item.lower()
    prev = None
    while prev != s:  # nested parens
        prev = s
        s = _PARENTHETICAL.sub(" ", s)
    s = _PUNCT.sub(" ", s)
    return _WS.sub(" ", s).strip()


def diet_signature(s: str) -> frozenset[str]:
    """The diet markers present in a string. Clustering guard only (D17).

    This is a *merge guard*, not a classifier: it exists to stop two strings
    from being clustered together when they disagree about meat or egg. It is
    intentionally allowed to return several markers at once ("veg chicken
    momos" -> {veg, non-veg}); all that matters is that strings differing on
    diet get different signatures.
    """
    s = s.lower()
    sig = set()
    if any(w in s for w in _NONVEG_MARKERS):
        sig.add("non-veg")
    if _EGG_RE.search(s):
        sig.add("egg")
    if _VEG_RE.search(s):
        sig.add("veg")
    return frozenset(sig)


# ---------------------------------------------------------------- clustering


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio() * 100


def token_sort_ratio(a: str, b: str) -> float:
    """Order-insensitive edit similarity. Catches spelling variants.

    token_set_ratio was evaluated and REJECTED: its subset behaviour merged
    "chicken paratha" and "mutton kebab paratha" into "paratha". See
    .notes/phase_1.md A3.
    """
    return _ratio(" ".join(sorted(a.split())), " ".join(sorted(b.split())))


def build_clusters(counts: dict[str, int], threshold: float = 85.0) -> dict[str, str]:
    """Map each normalised item string to a canonical representative.

    Greedy: strings are considered most-frequent first, so the canonical form is
    the commonest spelling. A string joins the first cluster it matches.

    D17 guard: two strings are never merged if their diet signatures differ.
    This generalises the veg/egg biryani case ("veg biryani" and "egg biryani"
    are one edit apart, so any edit-distance metric merges them) to every
    veg/egg/non-veg confusion, rather than blacklisting that single pair.
    """
    ordered = sorted(counts, key=lambda s: (-counts[s], s))
    canonical: list[str] = []
    sigs: dict[str, frozenset[str]] = {}
    mapping: dict[str, str] = {}

    for s in ordered:
        sig = diet_signature(s)
        match = None
        for c in canonical:
            if sigs[c] != sig:
                continue  # D17: refuse cross-diet merges
            if token_sort_ratio(s, c) >= threshold:
                match = c
                break
        if match is None:
            canonical.append(s)
            sigs[s] = sig
            mapping[s] = s
        else:
            mapping[s] = match
    return mapping
