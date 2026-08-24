"""Derive diet tags (veg / egg / nonveg / unclear) from item text.

Why a tagger at all: hall-level `tags` from the upstream API are unusable —
Hall 3 declares `tags: []` while serving non-veg in 10 of 21 slots (DECISIONS.md
D5). So the tag has to come from the item string.

This is a classifier, and its error rate is a hard ceiling on every tag-filtered
benchmark question. It is measured like one — see scripts/eval_tagger.py and
DECISIONS.md D20.

Design principles, fixed BEFORE looking at label performance:

1. An explicit `(Non-Veg)` marker in the raw string is authoritative. Upstream
   writes it, and where it is present there is nothing to infer.
2. Protein words are protein words. Preparation styles are NOT: `tikka`,
   `kebab`, `65`, `lollipop`, `momos`, `tandoori` all cut across diets
   (`Paneer Tikka`, `Veg Kabab`, `Paneer 65` are vegetarian).
3. `egg` is only an egg signal as a whole word. `bhurji` alone is not —
   `Aloo Bhurji` and `Paneer Bhurji` are vegetarian.
4. Some dish names are genuinely both. A bare `momos` or `manchurian` carries
   no diet information at all. The tagger ABSTAINS on these (`unclear`) rather
   than guessing, because a wrong tag silently corrupts a query answer while an
   abstention is visible.
5. Otherwise: veg. Not a coin flip — in this corpus non-veg is explicitly
   marked and the base menu is vegetarian by construction, so absence of any
   signal is itself weak evidence of veg.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

TAGS = ("veg", "egg", "nonveg", "unclear")

# (1) Explicit markers written by upstream.
_MARKER_NONVEG = re.compile(r"\(\s*non[\s-]*veg[^)]*\)|\bnon[\s-]*veg\b", re.I)
_MARKER_VEG = re.compile(r"\(\s*veg\s*\)", re.I)

# (2) Unambiguous animal proteins. Preparation styles are deliberately absent.
_NONVEG = re.compile(
    r"\b("
    r"chicken|murg|murgh|mutton|lamb|goat|beef|pork|ham|bacon|"
    r"fish|prawn|shrimp|crab|katla|rohu|pabda|hilsa|bhetki|surmai|tuna|"
    r"keema|qeema|kheema"
    r")\b", re.I)

# (3) Egg, whole word only.
_EGG = re.compile(r"\b(egg|eggs|anda|ande)\b|omelette|omlette|akuri", re.I)

# (4) Dish names that exist in both veg and non-veg forms. If one of these is
#     the dish and nothing qualifies it, the string is genuinely undetermined.
_AMBIGUOUS = re.compile(
    r"\b("
    r"biryani|biriyani|pulao|pulav|cutlet|roll|momo|momos|manchurian|"
    r"kebab|kabab|kabab|seekh|chaap|noodles|chowmein|chow mein|hakka|"
    r"sandwich|burger|stew|korma|shashlik|sausage|salami|handi|"
    r"lollipop|lolipop|65|tikka"
    r")\b", re.I)

# Qualifiers that resolve an ambiguous head to vegetarian.
_VEG_QUALIFIER_RE = re.compile(
    r"\b("
    r"veg|vegetable|vegetarian|paneer|tofu|soya|soyabean|soyabeen|mushroom|"
    r"aloo|alu|aaloo|potato|corn|cheese|dahi|curd|malai|gobi|cabbage|"
    r"matar|mattar|peas|chana|chole|chhole|dal|daal|moong|besan|sooji|suji|"
    r"atta|aata|maida|spring|palak|methi|kathal|jackfruit|banana|kaju|"
    r"badam|sattu|pyaz|pyaaz|onion|tomato|imli|pudina|mint|hara|sev|"
    r"noodle veg|crispy corn"
    r")\b", re.I)

# Fuzzy spelling tolerance, VEG QUALIFIERS ONLY.
#
# Upstream text is hand-transcribed and misspells qualifiers ("Vegetgable
# Pulav"), which makes an otherwise resolvable item abstain.
#
# This tolerance is deliberately NOT applied to the protein/egg lexicon. An
# inspection scan of all 572 corpus tokens against that lexicon at ratio >= 0.80
# returns 8 hits, and 6 of them are catastrophic:
#
#     'katli' ~ katla   -> "Kaju Katli" (a sweet)              would be nonveg
#     'kheera' ~ kheema -> "Kheera Raita" (cucumber)           would be nonveg
#     'kala' ~ katla    -> "Kala Chana Curry"                  would be nonveg
#     'cham' ~ ham      -> "Malai Cham Cham" (a sweet)         would be nonveg
#     'and' ~ anda      -> "Besan And Moong Dal Chila"         would be egg
#     'chila' ~ hilsa   -> "Besan And Moong Dal Chila"         would be nonveg
#
# The 2 genuine hits ('kalia', 'kaila' ~ katla) appear only in "Katla Kalia",
# "Rohu Kalia", "Katla Kaila" — every one of which already contains an exact
# protein token. So fuzzy protein matching gains nothing and would label sweets
# and vegetables as meat. Proteins stay exact-match, permanently.
#
# Direction of error also matters: a loose veg qualifier can only move an item
# from `unclear` to `veg`, never manufacture a non-veg claim.
_FUZZY_MIN_LEN = 6      # short tokens stay exact: 'veg'/'egg' are one edit apart
_FUZZY_RATIO = 0.90

_VEG_QUALIFIER_TOKENS = frozenset("""
veg vegetable vegetarian paneer tofu soya soyabean soyabeen mushroom aloo alu
aaloo potato corn cheese dahi curd malai gobi cabbage matar mattar peas chana
chole chhole dal daal moong besan sooji suji atta aata maida spring palak methi
kathal jackfruit banana kaju badam sattu pyaz pyaaz onion tomato imli pudina
mint hara sev
""".split())


def _has_veg_qualifier(s: str) -> bool:
    if _VEG_QUALIFIER_RE.search(s):
        return True
    for tok in re.findall(r"[a-z]+", s.lower()):
        if len(tok) < _FUZZY_MIN_LEN:
            continue
        # D17: never let a fuzzy match cross a diet line.
        if _NONVEG.search(tok) or _EGG.search(tok):
            continue
        for q in _VEG_QUALIFIER_TOKENS:
            if len(q) < _FUZZY_MIN_LEN:
                continue
            if SequenceMatcher(None, tok, q).ratio() >= _FUZZY_RATIO:
                return True
    return False


def tag(raw: str) -> str:
    """Classify one item string. `raw` should be the VERBATIM upstream string.

    Parentheticals must not be stripped first: two tag-critical strings hide
    their signal there —
        "Banana Shake (Instead Of Milk, Banana And Egg)"  -> egg
        "Veg & Chicken Momos (Non-Veg)"                   -> nonveg
    """
    s = raw or ""

    # 1. Named animal protein beats everything, including an egg word:
    #    "Chicken Egg Curry" is nonveg, not egg.
    if _NONVEG.search(s):
        return "nonveg"

    # 2. Egg is checked BEFORE the (Non-Veg) marker, deliberately.
    #    Upstream writes "Egg Biryani (Non-Veg)", "Egg Curry (Non-Veg)",
    #    "Egg Roll (Non-Veg)" — its marker means "not vegetarian" in a
    #    two-class sense. Under a four-class scheme where egg is its own
    #    class, those are `egg`. Taking the marker first would collapse egg
    #    into nonveg for exactly the dishes that need the distinction.
    if _EGG.search(s):
        return "egg"

    # 3. Explicit marker, for items whose protein is not in the lexicon
    #    ("Rajasthani Lal Maas", "Roasted Tangdi", "Kabab Roll").
    if _MARKER_NONVEG.search(s):
        return "nonveg"

    if _MARKER_VEG.search(s):
        return "veg"

    # 4. Ambiguous dish with nothing to disambiguate it -> abstain.
    if _AMBIGUOUS.search(s) and not _has_veg_qualifier(s):
        return "unclear"

    # 5. Default.
    return "veg"
