"""Naive RAG: retrieve -> prompt -> generate -> map citations -> instrument.

Deliberately naive. No query rewriting, no reranker, no metadata filtering, no
multi-hop. This is the baseline Phase 3 measures the router against, and it has
to be a fair one — see DECISIONS.md D22/D23/D24.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from .index import Index, Hit
from .llm import LLM, LLMResult, get_llm

DEFAULT_K = 5

SYSTEM = """\
You answer questions about IIT Kanpur hall mess menus using ONLY the numbered \
menu excerpts provided.

Rules:
- Use only the excerpts. If they do not contain the answer, say so plainly.
- Cite every factual claim with the excerpt number in square brackets, like [2].
- A claim about a specific hall must cite the excerpt for that hall.
- If the excerpts cover only some halls, say which ones you can see rather than \
implying the list is complete. Do not guess about halls that are not shown.
- Items marked "(Non-Veg)" are non-vegetarian. Do not assume anything else is.
- Be concise."""

CITATION_RE = re.compile(r"\[(\d+)\]")


@dataclass
class Citation:
    n: int
    chunk_id: str
    hall: str
    day: str
    meal: str


@dataclass
class QueryResult:
    query: str
    answer: str
    citations: list[Citation]
    hits: list[Hit]
    dropped_citations: list[int] = field(default_factory=list)

    # instrumentation
    k: int = DEFAULT_K
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    thinking_tokens: int = 0
    retries: int = 0
    retrieval_ms: float = 0.0
    generation_ms: float = 0.0
    total_ms: float = 0.0

    @property
    def retrieved_chunk_ids(self) -> list[str]:
        return [h.chunk.chunk_id for h in self.hits]

    def instrumentation_line(self) -> str:
        extra = f"  retries={self.retries}" if self.retries else ""
        return (f"k={self.k}  model={self.model}  "
                f"prompt_tokens={self.prompt_tokens}  "
                f"completion_tokens={self.completion_tokens}  "
                f"thinking_tokens={self.thinking_tokens}  "
                f"retrieval_ms={self.retrieval_ms:.1f}  "
                f"generation_ms={self.generation_ms:.0f}  "
                f"total_ms={self.total_ms:.0f}{extra}")


def build_prompt(query: str, hits: list[Hit]) -> str:
    blocks = [f"[{h.rank}] {h.chunk.text}" for h in hits]
    return ("Menu excerpts:\n\n" + "\n\n".join(blocks)
            + f"\n\nQuestion: {query}")


def map_citations(answer: str, hits: list[Hit]) -> tuple[list[Citation], list[int]]:
    """Map [n] back to (hall, day, meal). Invented numbers are dropped."""
    by_rank = {h.rank: h for h in hits}
    seen, cites, dropped = set(), [], []
    for m in CITATION_RE.finditer(answer):
        n = int(m.group(1))
        if n in seen:
            continue
        seen.add(n)
        hit = by_rank.get(n)
        if hit is None:
            dropped.append(n)          # hallucinated excerpt number
            continue
        c = hit.chunk
        cites.append(Citation(n=n, chunk_id=c.chunk_id, hall=c.hall,
                              day=c.day, meal=c.meal))
    return cites, sorted(dropped)


def strip_dropped(answer: str, dropped: list[int]) -> str:
    """Remove invented citation markers from the visible answer."""
    if not dropped:
        return answer
    bad = set(dropped)
    out = CITATION_RE.sub(
        lambda m: "" if int(m.group(1)) in bad else m.group(0), answer)
    return re.sub(r"[ \t]+([.,;])", r"\1", out).strip()


class NaiveRAG:
    def __init__(self, index: Index | None = None, llm: LLM | None = None,
                 k: int = DEFAULT_K):
        self.index = index or Index.load()
        self.llm = llm or get_llm()
        self.k = k

    def query(self, question: str, k: int | None = None) -> QueryResult:
        k = k or self.k
        t_start = time.perf_counter()

        t0 = time.perf_counter()
        hits = self.index.search(question, k=k)
        retrieval_ms = (time.perf_counter() - t0) * 1000

        prompt = build_prompt(question, hits)
        res: LLMResult = self.llm.complete(SYSTEM, prompt)

        cites, dropped = map_citations(res.text, hits)
        answer = strip_dropped(res.text, dropped)

        return QueryResult(
            query=question, answer=answer, citations=cites, hits=hits,
            dropped_citations=dropped, k=k, model=res.model,
            prompt_tokens=res.prompt_tokens,
            completion_tokens=res.completion_tokens,
            thinking_tokens=res.thinking_tokens, retries=res.retries,
            retrieval_ms=retrieval_ms, generation_ms=res.latency_ms,
            total_ms=(time.perf_counter() - t_start) * 1000,
        )
