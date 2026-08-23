# IIT Khana

Question answering over IIT Kanpur hall mess menus.

**Thesis:** mess menus are structured data. Most real questions about them
("which mess has chicken tonight", "which halls do *not* serve chicken at
dinner", "how many halls serve paneer at dinner on Tuesday") are filters and
aggregations, not semantic search. Naive RAG is the wrong tool. This project
builds a two-path query router — SQL over SQLite for structured questions,
embedding retrieval for fuzzy ones — and **measures** it against a naive RAG
baseline on a hand-built benchmark.

The benchmark and the results table are the point. The app is the delivery
vehicle.

## Status

**Phase 1 of 8 — ingestion and normalization.**
Database populated (294/294 slots, 1,903 items). Tagger evaluation in progress.
See [DECISIONS.md](DECISIONS.md) for scope, data model, and architecture.

## What this benchmark measures

**Faithfulness to the corpus, not truth about IIT Kanpur dining.** A correct
answer reflects what campusmess.in records — not what a hall actually serves.
The gap is real and measured: halls transcribe only what *varies*, so staples
go unlisted (no hall lists drinking water; all serve it), and transcription
density varies nearly 2x between halls. See DECISIONS.md D16.

## Data

Public JSON API at `https://campusmess.in/api`. 14 IIT Kanpur halls,
7 days x 3 meals each = **294 menu rows**, splitting to **1,903 items**
(931 distinct, 776 after clustering). The menu is a repeating weekly cycle;
there are no calendar dates and no serving times in the source.

    python -m scripts.ingest --per-hall     # cached data -> SQLite + coverage report
    python -m scripts.export_labels         # export items for hand labelling
    pytest tests/ -q

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
