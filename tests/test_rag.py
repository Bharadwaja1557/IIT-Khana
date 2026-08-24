"""Phase 2 naive RAG tests.

No network and no LLM calls. Retrieval quality is measured separately by
scripts/retrieval_recall.py; these pin behaviour, not scores.
"""

from __future__ import annotations

import numpy as np
import pytest

from rag.chunk import build_chunks
from rag.index import Index
from rag.llm import EchoLLM, LLMResult, RETRY_STATUS, with_backoff
from rag.pipeline import (Citation, NaiveRAG, build_prompt, map_citations,
                          strip_dropped)


# ------------------------------------------------------------------ chunking

@pytest.fixture(scope="module")
def chunks():
    return build_chunks()


def test_one_chunk_per_slot(chunks):
    assert len(chunks) == 294                      # 14 halls x 7 days x 3 meals
    assert len({c.chunk_id for c in chunks}) == 294


def test_chunk_carries_hall_day_meal(chunks):
    c = next(c for c in chunks if c.chunk_id == "hall-12__wednesday__dinner")
    assert "Hall 12" in c.text
    assert "Wednesday Dinner" in c.text


def test_chunk_keeps_nonveg_markers_verbatim(chunks):
    """Stripping upstream's (Non-Veg) marker would cripple the baseline on
    exactly the questions that matter. D22 fairness constraint."""
    c = next(c for c in chunks if c.chunk_id == "hall-12__wednesday__dinner")
    assert "Mutton Rogan Josh (Non-Veg)" in c.text


def test_chunk_carries_hall_metadata(chunks):
    """Without this, D8 category 7 (policy) is unanswerable by construction."""
    c = next(c for c in chunks if c.hall == "GH 1")
    assert "Girls hall" in c.text


def test_chunk_excludes_derived_diet_tags(chunks):
    """Derived tags are the structured path's enrichment (D22). Handing them to
    a 'naive' baseline would flatter it, not test it."""
    blob = "\n".join(c.text for c in chunks).lower()
    for line in blob.splitlines():
        assert not line.strip().startswith("tags:")


def test_base_and_extras_labelled_separately(chunks):
    c = next(c for c in chunks if c.chunk_id == "hall-12__wednesday__dinner")
    assert "Menu:" in c.text and "Extras:" in c.text


# ------------------------------------------------------------------- index

@pytest.fixture(scope="module")
def index():
    try:
        return Index.load()
    except FileNotFoundError:
        pytest.skip("index not built; run python -m scripts.build_index")


def test_vectors_are_l2_normalised(index):
    """Cosine == dot product depends on this. D23."""
    norms = np.linalg.norm(index.vecs, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-4)


def test_index_shape_matches_chunks(index):
    assert index.vecs.shape[0] == len(index.chunks) == 294
    assert index.vecs.shape[1] == 384


def test_search_returns_k_ranked_descending(index):
    hits = index.search("paneer at dinner", k=7)
    assert len(hits) == 7
    assert [h.rank for h in hits] == list(range(1, 8))
    assert all(a.score >= b.score for a, b in zip(hits, hits[1:]))


def test_search_k_larger_than_corpus_is_clamped(index):
    assert len(index.search("dal", k=999)) == 294


# --------------------------------------------------------------- citations

class _Hit:
    def __init__(self, rank, chunk):
        self.rank, self.chunk, self.score = rank, chunk, 0.5


def _hits(chunks, n=3):
    return [_Hit(i + 1, chunks[i]) for i in range(n)]


def test_citations_map_to_hall_day_meal(chunks):
    hits = _hits(chunks)
    cites, dropped = map_citations("Foo [1] and bar [3].", hits)
    assert dropped == []
    assert [c.n for c in cites] == [1, 3]
    assert cites[0].hall == chunks[0].hall
    assert cites[0].meal == chunks[0].meal


def test_invented_citation_is_dropped(chunks):
    """An excerpt number that was never in the prompt must not be shown."""
    hits = _hits(chunks, 3)
    cites, dropped = map_citations("Real [2] but invented [9].", hits)
    assert dropped == [9]
    assert [c.n for c in cites] == [2]


def test_dropped_citation_removed_from_visible_answer():
    out = strip_dropped("Real [2] but invented [9].", [9])
    assert "[9]" not in out
    assert "[2]" in out


def test_repeated_citation_counted_once(chunks):
    cites, _ = map_citations("[1] and again [1].", _hits(chunks))
    assert len(cites) == 1


def test_prompt_numbers_chunks_from_one(chunks):
    p = build_prompt("q?", _hits(chunks, 3))
    assert "[1] " in p and "[2] " in p and "[3] " in p
    assert p.rstrip().endswith("Question: q?")


# --------------------------------------------------------------- pipeline

def test_pipeline_end_to_end_offline(index):
    r = NaiveRAG(index=index, llm=EchoLLM(), k=4).query("chicken dinner")
    assert len(r.hits) == 4
    assert len(r.retrieved_chunk_ids) == 4
    assert r.prompt_tokens > 0
    assert r.total_ms >= 0
    assert r.model == "echo"


def test_instrumentation_reports_thinking_separately():
    """Gemini thinking averaged 5.4x completion tokens; folding them into
    completion_tokens would understate cost fivefold. D24."""
    from rag.pipeline import QueryResult
    line = QueryResult(query="q", answer="a", citations=[], hits=[],
                       completion_tokens=108, thinking_tokens=582
                       ).instrumentation_line()
    assert "completion_tokens=108" in line
    assert "thinking_tokens=582" in line


# ----------------------------------------------------------------- backoff

def test_backoff_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr("rag.llm.time.sleep", lambda s: None)

    class Boom(Exception):
        code = 429

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise Boom()
        return "ok"

    result, retries = with_backoff(flaky)
    assert result == "ok" and retries == 2


def test_backoff_does_not_retry_non_retryable(monkeypatch):
    monkeypatch.setattr("rag.llm.time.sleep", lambda s: None)

    class Bad(Exception):
        code = 400

    def always():
        raise Bad()

    with pytest.raises(Bad):
        with_backoff(always)


def test_rate_limit_status_is_retryable():
    assert 429 in RETRY_STATUS and 503 in RETRY_STATUS
