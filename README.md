# IIT Khana

Question answering over IIT Kanpur hall mess menus.

**Thesis:** mess menus are structured data. Most real questions about them
("which mess has chicken tonight", "which halls do *not* serve rice at lunch",
"how many halls serve paneer at dinner on Tuesday") are filters and
aggregations, not semantic search. Naive RAG is the wrong tool. This project
builds a two-path query router — SQL over SQLite for structured questions,
embedding retrieval for fuzzy ones — and **measures** it against a naive RAG
baseline on a hand-built benchmark.

The benchmark and the results table are the point. The app is the delivery
vehicle.

## Status

**Phase 0 of 8 — scope lock. No implementation yet.**
See [DECISIONS.md](DECISIONS.md) for scope, data model, and architecture.

## Data

Public JSON API at `https://campusmess.in/api`. 14 IIT Kanpur halls,
7 days x 3 meals each = **294 current menu rows**. The menu is a repeating
weekly cycle; there are no calendar dates and no serving times in the source.

## Layout

    ingestion/   fetch + normalise + load from the campusmess.in API
    db/          SQLite schema and queries (the structured path)
    rag/         embedding index + the naive-RAG baseline
    router/      question classifier and dispatcher
    eval/        benchmark questions, gold answers, scoring harness
    api/         HTTP interface
    scripts/     one-off operational scripts
    tests/

## Setup

    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env    # then fill in

## Attribution

Menu data comes from [campusmess.in](https://campusmess.in), a third-party
student project. This repo is not affiliated with it or with IIT Kanpur.
Ingestion is rate-limited to 1 request/second and fully cached — see
"Scraping posture" in [DECISIONS.md](DECISIONS.md).
