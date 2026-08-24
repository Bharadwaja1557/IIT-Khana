"""Diet tagger tests.

These pin the *design rules* (DECISIONS.md D20), not the eval-set score. The
tagger is scored separately by scripts/eval_tagger.py against hand labels.
"""

from __future__ import annotations

import pytest

from ingestion.tagger import tag


# --------------------------------------------------- explicit upstream markers


@pytest.mark.parametrize("raw", [
    "Chicken Lolipop (Non-Veg)",
    "Mustard Fish (Non-Veg)",
    "Mutton Rogan Josh (Non-Veg)",
    "Fish Fry + Non-veg Dal (Non-Veg)",
])
def test_explicit_marker_is_nonveg(raw):
    assert tag(raw) == "nonveg"


@pytest.mark.parametrize("raw", [
    "Rajasthani Lal Maas (Non-Veg)",   # 'maas' not in the protein lexicon
    "Roasted Tangdi (Non-Veg)",        # 'tangdi' not in the lexicon
    "Kabab Roll (Non-Veg)",            # 'kabab' is a prep style, not a protein
])
def test_marker_carries_items_whose_protein_is_not_in_the_lexicon(raw):
    """The marker exists precisely so we do not need an exhaustive lexicon."""
    assert tag(raw) == "nonveg"


# ------------------------------------------------------------ named proteins


@pytest.mark.parametrize("raw,expected", [
    ("Butter Chicken", "nonveg"),          # unmarked, caught by lexicon
    ("Chilli Chicken", "nonveg"),
    ("Chicken Kali Mirch", "nonveg"),
    ("Katla Kaila", "nonveg"),             # katla = fish, misspelt upstream
    ("Murgh Rogan Josh (Non-Veg) Prior Booking Option", "nonveg"),
    ("Mutton Kosha (Non-Veg)", "nonveg"),
])
def test_named_proteins(raw, expected):
    assert tag(raw) == expected


# ------------------------- preparation styles are NOT protein signals (D17/D20)


@pytest.mark.parametrize("raw", [
    "Paneer Tikka",      # tikka
    "Veg Kabab",         # kabab
    "Paneer 65",         # 65
    "Dahi Kebab",        # kebab, and dahi resolves it
    "Veg Momos",         # momos
    "Soyabean Chaap Gravy",
])
def test_preparation_styles_are_not_meat(raw):
    assert tag(raw) == "veg"


# --------------------------------------------------------------------- egg


@pytest.mark.parametrize("raw", [
    "Egg Curry",
    "Egg Roll",
    "Banana Shake (Instead Of Milk, Banana And Egg)",  # signal inside parens
])
def test_egg_detected(raw):
    assert tag(raw) == "egg"


@pytest.mark.parametrize("raw", [
    "Egg Biryani (Non-Veg)",
    "Egg Curry (Non-Veg)",
    "Egg Roll (Non-Veg)",
])
def test_egg_beats_the_nonveg_marker(raw):
    """Upstream's (Non-Veg) means 'not vegetarian' in a two-class sense. Under a
    four-class scheme these are egg, and taking the marker first would collapse
    egg into nonveg for exactly the dishes that need the distinction."""
    assert tag(raw) == "egg"


def test_named_protein_beats_egg():
    assert tag("Chicken Egg Curry") == "nonveg"


@pytest.mark.parametrize("raw", [
    "Aloo Bhurji",       # potato, not egg — 'bhurji' alone is not an egg word
    "Paneer Bhurji",
])
def test_bhurji_alone_is_not_egg(raw):
    assert tag(raw) == "veg"


# ---------------------------------------------------------------- abstention


@pytest.mark.parametrize("raw", ["Momos", "Manchurian", "Kathi Roll", "Basanti Pulav"])
def test_abstains_on_genuinely_ambiguous_dishes(raw):
    """A wrong tag silently corrupts a query answer; an abstention is visible."""
    assert tag(raw) == "unclear"


@pytest.mark.parametrize("raw,expected", [
    ("Veg Cutlet", "veg"),
    ("Paneer Biryani", "veg"),
    ("Veg Chowmein", "veg"),
    ("Chicken Biryani (Non-Veg)", "nonveg"),
    ("Egg Biryani", "egg"),
])
def test_qualifier_resolves_an_ambiguous_head(raw, expected):
    assert tag(raw) == expected


# ------------------------------------------------------------------- default


@pytest.mark.parametrize("raw", ["Arhar Dal", "Sambhar", "Poha", "Jeera Rice", "Curd"])
def test_unsignalled_items_default_to_veg(raw):
    assert tag(raw) == "veg"


def test_empty_input_does_not_crash():
    assert tag("") == "veg"
    assert tag(None) == "veg"


# ------------------------------------------- typo tolerance, veg qualifiers only


def test_misspelt_veg_qualifier_still_resolves():
    """Upstream hand-transcribes; "Vegetgable Pulav" should not abstain."""
    assert tag("Vegetgable Pulav") == "veg"
    assert tag("Seasonal Vegetables") == "veg"


@pytest.mark.parametrize("raw,expected", [
    ("Kaju Katli", "veg"),                    # 'katli' ~ katla
    ("Kheera Raita", "veg"),                  # 'kheera' ~ kheema
    ("Kala Chana Curry", "veg"),              # 'kala'   ~ katla
    ("Malai Cham Cham", "veg"),               # 'cham'   ~ ham
    ("Besan And Moong Dal Chila", "veg"),     # 'and' ~ anda, 'chila' ~ hilsa
])
def test_protein_lexicon_is_never_fuzzy_matched(raw, expected):
    """Fuzzy-matching proteins would label these sweets and vegetables as meat.
    Proteins stay exact-match permanently — see the comment in tagger.py."""
    assert tag(raw) == expected


def test_fuzzy_qualifier_does_not_cross_diet_lines():
    """D17: 'veg' and 'egg' are one edit apart, so short tokens stay exact."""
    assert tag("Egg Roll") == "egg"
    assert tag("Veg Roll") == "veg"
