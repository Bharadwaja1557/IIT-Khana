"""Parser tests against REAL menu strings pulled from the recon cache.

Every fixture in tests/fixtures/menu_rows.json is a verbatim upstream row,
chosen because it exercises an edge case found during Phase 1 Part A. The
expected values below were checked by hand against the source text.

If the parser is wrong, the gold answers in the benchmark are wrong and the
whole results table is meaningless — so these assertions are exact, not smoke.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ingestion.parse import (
    build_clusters,
    diet_signature,
    normalize,
    split_items,
    unwrap_outer_parens,
)

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "menu_rows.json").read_text())


def row(hall: str, day: str, meal: str) -> dict:
    for r in FIXTURES:
        if (r["hall"], r["dayOfWeek"], r["mealType"]) == (hall, day, meal):
            return r
    raise KeyError(f"fixture {hall}/{day}/{meal} not found")


# --------------------------------------------------------- fully-parenthesised


def test_fully_parenthesised_description_splits():
    """Regression: '(A, B)' returned ONE item before the unwrap fix."""
    desc = row("Hall 11", "Tuesday", "Dinner")["description"]
    assert desc == "(Aloo Baigan Light Gravy, Mix Dal)"
    assert split_items(desc) == ["Aloo Baigan Light Gravy", "Mix Dal"]


def test_nested_fully_parenthesised_description_splits():
    """The wrapper must be stripped without disturbing inner parentheses."""
    desc = row("Hall 8", "Thursday", "Breakfast")["description"]
    items = split_items(desc)
    assert items == ["Ajwain Poori", "Roti", "Matar (Yellow) Sabzi", "Halwa"]


def test_unwrap_leaves_non_wrapping_parens_alone():
    # These parens do NOT enclose the whole string.
    s = "(Aloo) Sabzi, Dal"
    assert unwrap_outer_parens(s) == s
    assert split_items(s) == ["(Aloo) Sabzi", "Dal"]


# ------------------------------------------------------------------ newlines


def test_newline_is_a_separator():
    """12 rows (all Hall 11) use newlines instead of commas."""
    extras = row("Hall 11", "Tuesday", "Dinner")["extras"]
    assert "\n" in extras
    assert split_items(extras) == ["Fish Tikka (Non-Veg)", "Aloo Tikki Chaat", "Chenna"]


def test_newline_and_comma_mix():
    extras = row("Hall 11", "Wednesday", "Lunch")["extras"]
    assert split_items(extras) == ["Dahi Katla (Non-Veg)", "Mushroom Paneer Dry",
                                   "Shahi Tukda"]


# -------------------------------------------------------------- alternatives


def test_slash_and_or_alternatives_expand():
    desc = row("Hall 14", "Sunday", "Dinner")["description"]
    items = split_items(desc)
    assert items[:3] == ["Kadhai Paneer", "Paneer Butter Masala",
                         "Chicken Kali Mirch (Non-Veg)"]
    assert "Gulab Jamun" in items


def test_slash_inside_a_comma_field():
    desc = row("Hall 7", "Monday", "Lunch")["description"]
    items = split_items(desc)
    # "Fruits/Curd/ Boondi Raita" -> three items, trailing space handled
    assert "Fruits" in items and "Curd" in items and "Boondi Raita" in items


# --------------------------------------- separators we deliberately do NOT split


@pytest.mark.parametrize("text,expected", [
    # "&" inside parens: one dish, its variants
    ("Chapati (Plain & Deshi Ghee)", ["Chapati (Plain & Deshi Ghee)"]),
    # "&" joining a compound dish name — splitting would invent "Besan"
    ("Besan & Moong Dal Chilla", ["Besan & Moong Dal Chilla"]),
    # "and" as part of one dish
    ("Fried And Roasted Papad", ["Fried And Roasted Papad"]),
    # "with" means "served with", not "and also"
    ("Sambhar With Coconut Chutney", ["Sambhar With Coconut Chutney"]),
    # "+" is an ingredient joiner
    ("Aloo+Capsicum+Matar", ["Aloo+Capsicum+Matar"]),
    ("Mutton Biryani (Non-Veg) + Salan", ["Mutton Biryani (Non-Veg) + Salan"]),
])
def test_ambiguous_joiners_are_not_separators(text, expected):
    assert split_items(text) == expected


def test_veg_and_chicken_momos_stays_whole():
    """Splitting on '&' would yield the meaningless item 'Veg' and lose the
    distributive sense. Left whole, it still tags non-veg."""
    assert split_items("Veg & Chicken Momos (Non-Veg)") == ["Veg & Chicken Momos (Non-Veg)"]


# ------------------------------------------------------------------- empties


@pytest.mark.parametrize("value", [None, "", "   ", ",", " , , "])
def test_empty_fields_yield_no_items(value):
    assert split_items(value) == []


def test_null_extras_yields_nothing():
    assert row("Hall 14", "Sunday", "Dinner")["extras"] is None
    assert split_items(None) == []


# ----------------------------------------------------------------- normalize


@pytest.mark.parametrize("raw,expected", [
    ("Jeera-Rice", "jeera rice"),
    ("Chicken Kali Mirch (Non-Veg)", "chicken kali mirch"),
    ("Chapati (Plain & Deshi Ghee)", "chapati"),
    ("Matar (Yellow) Sabzi", "matar sabzi"),
    ("  Mix-Veg  ", "mix veg"),
    ("Paneer 65", "paneer 65"),
])
def test_normalize(raw, expected):
    assert normalize(raw) == expected


def test_normalize_strips_nested_parentheticals():
    assert normalize("Poori (Aata (Fine) & Maida)") == "poori"


# --------------------------------------------------- D17 clustering guard


def test_clustering_merges_spelling_variants():
    counts = {"sambhar": 27, "sambar": 7, "arhar dal": 21, "arhar daal": 5}
    m = build_clusters(counts, threshold=85.0)
    assert m["sambar"] == "sambhar"          # commoner spelling wins
    assert m["arhar daal"] == "arhar dal"


def test_clustering_never_merges_across_diet_lines():
    """D17. 'veg biryani' and 'egg biryani' are one edit apart, so any
    edit-distance metric merges them. The diet-signature guard must refuse."""
    counts = {"veg biryani": 8, "egg biryani": 5}
    m = build_clusters(counts, threshold=85.0)
    assert m["veg biryani"] != m["egg biryani"]


def test_clustering_guard_generalises_beyond_biryani():
    counts = {"veg cutlet": 4, "egg cutlet": 3, "chicken cutlet": 2}
    m = build_clusters(counts, threshold=80.0)
    assert len({m["veg cutlet"], m["egg cutlet"], m["chicken cutlet"]}) == 3


def test_clustering_guard_allows_same_diet_merges():
    counts = {"veg biryani": 8, "veg biriyani": 2}
    m = build_clusters(counts, threshold=85.0)
    assert m["veg biriyani"] == m["veg biryani"]


@pytest.mark.parametrize("text,expected", [
    ("chicken kali mirch", {"non-veg"}),
    ("egg biryani", {"egg"}),
    ("veg biryani", {"veg"}),
    ("aloo bhujiya", set()),
    # Preparation styles must NOT read as meat: these are vegetarian dishes.
    ("paneer tikka", {"veg"}),
    ("veg kabab", {"veg"}),
])
def test_diet_signature(text, expected):
    assert diet_signature(text) == frozenset(expected)
