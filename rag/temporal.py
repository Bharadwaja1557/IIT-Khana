"""Shared temporal preprocessing. Runs BEFORE the fork — see DECISIONS.md D27.

Date/day resolution is available identically to naive RAG, long-context stuffing
and (in Phase 4) the structured router. If only the router got the resolved day,
it would win the temporal category because it was told the answer's slot and the
baselines were not — measuring the experimental setup, not the architecture.

Two outputs, both shared:
  resolved_query  relative terms rewritten to concrete weekdays. Retrieval
                  embeds THIS, not the raw string — otherwise retrieval is
                  denied information the router would have.
  context_line    "Today is Tuesday, 2026-08-25." prepended to every prompt.

Reproducibility: `today` is always passed explicitly. "tonight" resolved against
the wall clock would give a benchmark whose gold answers change daily.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
        "Saturday", "Sunday"]
MEALS = ["Breakfast", "Lunch", "Dinner"]

# Relative day expressions -> offset in days from `today`.
_REL_DAY = {
    "today": 0, "tonight": 0, "this evening": 0, "this afternoon": 0,
    "this morning": 0, "right now": 0,
    "tomorrow": 1, "tomorrow night": 1,
    "yesterday": -1,
}

# Expressions that also imply a meal.
_REL_MEAL = {
    "tonight": "Dinner", "this evening": "Dinner", "tomorrow night": "Dinner",
    "this morning": "Breakfast", "this afternoon": "Lunch",
}

_MEAL_WORDS = {
    "breakfast": "Breakfast", "brunch": "Breakfast",
    "lunch": "Lunch", "dinner": "Dinner", "supper": "Dinner",
}

# Longest-first so "tomorrow night" beats "tomorrow" and "this evening" beats
# a bare day word.
_REL_RE = re.compile(
    r"\b(" + "|".join(sorted((set(_REL_DAY) | set(_REL_MEAL)),
                             key=len, reverse=True)).replace(" ", r"\s+") + r")\b",
    re.I)
_DAY_RE = re.compile(r"\b(" + "|".join(DAYS) + r")\b", re.I)
_MEAL_RE = re.compile(r"\b(" + "|".join(_MEAL_WORDS) + r")\b", re.I)


@dataclass(frozen=True)
class TemporalContext:
    today: date
    today_day: str                 # weekday name of `today`
    resolved_query: str            # what retrieval embeds and the prompt asks
    resolved_day: str | None       # concrete weekday the question refers to
    resolved_meal: str | None
    matched: tuple[str, ...]       # relative expressions that were rewritten

    @property
    def was_relative(self) -> bool:
        return bool(self.matched)

    def context_line(self) -> str:
        line = f"Today is {self.today_day}, {self.today.isoformat()}."
        if self.was_relative and self.resolved_day:
            what = self.resolved_day
            if self.resolved_meal:
                what += f" {self.resolved_meal.lower()}"
            line += (f' The question\'s "{self.matched[0]}" therefore refers to '
                     f"{what}.")
        return line


def resolve(query: str, today: date) -> TemporalContext:
    """Rewrite relative day/meal expressions in `query` against `today`."""
    today_day = DAYS[today.weekday()]
    matched: list[str] = []
    day: str | None = None
    meal: str | None = None

    def _sub(m: re.Match) -> str:
        nonlocal day, meal
        raw = m.group(0)
        key = re.sub(r"\s+", " ", raw.lower())
        matched.append(raw)
        offset = _REL_DAY.get(key, 0)
        d = DAYS[(today + timedelta(days=offset)).weekday()]
        day = day or d
        m2 = _REL_MEAL.get(key)
        if m2 and meal is None:
            meal = m2
            return f"on {d} at {m2.lower()}"
        return f"on {d}"

    resolved = _REL_RE.sub(_sub, query)

    # An explicit weekday in the question wins over anything inferred.
    explicit = _DAY_RE.search(query)
    if explicit:
        day = explicit.group(1).capitalize()

    if meal is None:
        mm = _MEAL_RE.search(resolved)
        if mm:
            meal = _MEAL_WORDS[mm.group(1).lower()]

    # Tidy "on Tuesday at dinner for dinner"-style doubling introduced by the
    # rewrite sitting next to an existing meal word.
    resolved = re.sub(r"\s{2,}", " ", resolved).strip()

    return TemporalContext(
        today=today, today_day=today_day, resolved_query=resolved,
        resolved_day=day, resolved_meal=meal, matched=tuple(matched),
    )
