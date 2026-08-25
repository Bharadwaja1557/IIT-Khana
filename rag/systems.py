"""The systems under test, sharing one answer contract.

Phase 3 compares systems, so everything that is not the system under test is held
identical: the same LLM interface, the same model, the same [n] citation
contract, the same instrumentation, and the same shared temporal preprocessing
(D27). The ONLY difference between naive RAG and long-context stuffing is which
chunks reach the prompt.

  NaiveRAG        top-k by cosine similarity          (k=5 -> ~425 prompt tokens)
  LongContext     all 294 slots, fixed order          (~17,100 prompt tokens)

Phase 4 adds the structured router here, on the same contract.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date

from .chunk import Chunk, build_chunks
from .index import Hit, Index
from .llm import LLM, LLMResult, get_llm
from .temporal import TemporalContext, resolve

DEFAULT_K = 5

# One prompt contract for every system. Changing this changes every system at
# once, which is the point — see integrity rule 2.
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
- When asked which halls satisfy a condition, name every hall that does, \
explicitly, by name.
- Be concise.

End your reply with ONE final line, exactly in this form:

ANSWER: <comma-separated list>

For a question asking WHICH HALLS, list the hall names (e.g. "ANSWER: Hall 2, \
GH 1"). For a question asking what is on one hall's menu, list the item names. \
If the answer is empty, write "ANSWER: none". The line must contain only the \
list, with no explanation."""


@dataclass
class Citation:
    n: int
    chunk_id: str
    hall: str
    day: str
    meal: str


@dataclass
class SystemAnswer:
    system: str
    query: str                     # the question as asked
    resolved_query: str            # after shared temporal preprocessing
    context_line: str
    answer: str
    citations: list[Citation]
    retrieved_chunk_ids: list[str]
    dropped_citations: list[int] = field(default_factory=list)

    k: int | None = None
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    thinking_tokens: int = 0
    cached_tokens: int = 0
    retries: int = 0
    retrieval_ms: float = 0.0
    generation_ms: float = 0.0
    total_ms: float = 0.0

    def instrumentation_line(self) -> str:
        bits = [f"system={self.system}", f"model={self.model}"]
        if self.k is not None:
            bits.append(f"k={self.k}")
        bits += [f"prompt={self.prompt_tokens}", f"completion={self.completion_tokens}",
                 f"thinking={self.thinking_tokens}", f"cached={self.cached_tokens}",
                 f"retrieval_ms={self.retrieval_ms:.1f}",
                 f"generation_ms={self.generation_ms:.0f}",
                 f"total_ms={self.total_ms:.0f}"]
        if self.retries:
            bits.append(f"retries={self.retries}")
        return "  ".join(bits)


def build_prompt(context_line: str, question: str, chunks: list[Chunk]) -> str:
    blocks = [f"[{i + 1}] {c.text}" for i, c in enumerate(chunks)]
    return (f"{context_line}\n\nMenu excerpts:\n\n" + "\n\n".join(blocks)
            + f"\n\nQuestion: {question}")


def map_citations(answer: str, chunks: list[Chunk]) -> tuple[list[Citation], list[int]]:
    """Map [n] back to (hall, day, meal). Invented numbers are dropped."""
    import re
    seen, cites, dropped = set(), [], []
    for m in re.finditer(r"\[(\d+)\]", answer):
        n = int(m.group(1))
        if n in seen:
            continue
        seen.add(n)
        if not (1 <= n <= len(chunks)):
            dropped.append(n)
            continue
        c = chunks[n - 1]
        cites.append(Citation(n, c.chunk_id, c.hall, c.day, c.meal))
    return cites, sorted(dropped)


def strip_dropped(answer: str, dropped: list[int]) -> str:
    if not dropped:
        return answer
    import re
    bad = set(dropped)
    out = re.sub(r"\[(\d+)\]",
                 lambda m: "" if int(m.group(1)) in bad else m.group(0), answer)
    return re.sub(r"[ \t]+([.,;])", r"\1", out).strip()


class _Base:
    name = "base"

    def __init__(self, llm: LLM | None = None):
        self.llm = llm or get_llm()

    def _select(self, ctx: TemporalContext) -> tuple[list[Chunk], float, int | None]:
        raise NotImplementedError

    def query(self, question: str, today: date) -> SystemAnswer:
        t_start = time.perf_counter()
        ctx = resolve(question, today)
        chunks, retrieval_ms, k = self._select(ctx)

        prompt = build_prompt(ctx.context_line(), ctx.resolved_query, chunks)
        res: LLMResult = self.llm.complete(SYSTEM, prompt)

        cites, dropped = map_citations(res.text, chunks)
        return SystemAnswer(
            system=self.name, query=question, resolved_query=ctx.resolved_query,
            context_line=ctx.context_line(),
            answer=strip_dropped(res.text, dropped),
            citations=cites, dropped_citations=dropped,
            retrieved_chunk_ids=[c.chunk_id for c in chunks],
            k=k, model=res.model,
            prompt_tokens=res.prompt_tokens,
            completion_tokens=res.completion_tokens,
            thinking_tokens=res.thinking_tokens,
            cached_tokens=res.cached_tokens,
            retries=res.retries,
            retrieval_ms=retrieval_ms, generation_ms=res.latency_ms,
            total_ms=(time.perf_counter() - t_start) * 1000,
        )


class NaiveRAG(_Base):
    name = "naive_rag"

    def __init__(self, index: Index | None = None, llm: LLM | None = None,
                 k: int = DEFAULT_K):
        super().__init__(llm)
        self.index = index or Index.load()
        self.k = k

    def _select(self, ctx):
        t0 = time.perf_counter()
        # Retrieval embeds the RESOLVED query (D27). Embedding the raw
        # "tonight" would deny retrieval information the router will have.
        hits: list[Hit] = self.index.search(ctx.resolved_query, k=self.k)
        return [h.chunk for h in hits], (time.perf_counter() - t0) * 1000, self.k


class LongContext(_Base):
    name = "long_context"

    def __init__(self, chunks: list[Chunk] | None = None, llm: LLM | None = None):
        super().__init__(llm)
        # Fixed order, built once. Stable ordering keeps the prompt prefix
        # byte-identical across queries, which is what makes caching possible
        # at all (D28) and keeps citation numbers stable across the run.
        self.chunks = chunks if chunks is not None else build_chunks()

    def _select(self, ctx):
        return self.chunks, 0.0, None
