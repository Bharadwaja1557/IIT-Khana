"""LLM provider interface. One seam, so the provider is swappable.

Phase 3 compares three systems (naive RAG, long-context stuffing, the structured
router) on accuracy AND tokens AND latency. Those numbers are only comparable if
every system calls the model the same way, so generation settings live here and
nowhere else.
"""

from __future__ import annotations

import os
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"

# NOTE: `gemini-2.5-flash` is listed by models.list() but returns
#   404 "no longer available to new users ... use models/gemini-3.6-flash"
# on a newly issued API key. 3.6-flash is the API's own named replacement.
DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"

# A cited menu answer is short. 4096 is far above the longest plausible answer
# (14 halls with citations) while still capping a runaway generation.
MAX_TOKENS = 4096

# Free-tier Gemini has per-minute and per-day caps, and Phase 3 will run ~300
# queries. Retry 429 (rate limit) and 503 (capacity) with exponential backoff.
RETRY_STATUS = (429, 503, 500, 502, 504)
MAX_RETRIES = 6
BASE_DELAY = 2.0
MAX_DELAY = 64.0


@dataclass(frozen=True)
class LLMResult:
    text: str
    prompt_tokens: int
    completion_tokens: int
    model: str
    latency_ms: float
    # Reasoning tokens, billed as output and reported SEPARATELY so they are
    # never silently folded into the answer's token count. Gemini 3.x thinks by
    # default: a bare "reply OK" cost 1 answer token and 116 thinking tokens.
    thinking_tokens: int = 0
    # Cached input tokens, for the D28 caching confound. Read from the API,
    # never assumed: the 'uncached' cost column must be labelled from what we
    # observe, not from what we intended.
    cached_tokens: int = 0
    retries: int = 0


class LLM(ABC):
    """Swappable provider seam."""

    name: str

    @abstractmethod
    def complete(self, system: str, user: str) -> LLMResult:
        ...


def _status_of(exc: Exception) -> int | None:
    """Best-effort HTTP status from a provider exception."""
    for attr in ("code", "status_code"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    resp = getattr(exc, "response", None)
    v = getattr(resp, "status_code", None)
    return v if isinstance(v, int) else None


def _retry_after(exc: Exception) -> float | None:
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None) or {}
    try:
        return float(headers.get("retry-after"))
    except (TypeError, ValueError):
        return None


def with_backoff(fn, *, max_retries: int = MAX_RETRIES):
    """Call `fn()`, retrying retryable statuses with exponential backoff.

    Returns (result, n_retries). Honours a `retry-after` header when present,
    otherwise 2^n seconds with full jitter, capped at MAX_DELAY.
    """
    last = None
    for attempt in range(max_retries + 1):
        try:
            return fn(), attempt
        except Exception as e:                       # noqa: BLE001 - provider-agnostic
            status = _status_of(e)
            if status not in RETRY_STATUS or attempt == max_retries:
                raise
            last = e
            delay = _retry_after(e)
            if delay is None:
                delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
                delay = random.uniform(delay / 2, delay)   # full jitter
            time.sleep(delay)
    raise last                                       # pragma: no cover


class GeminiLLM(LLM):
    """Google Gemini via the `google-genai` SDK.

    Thinking is left at the model default rather than disabled. Phase 3 compares
    three systems on tokens and latency, so the setting must be identical across
    all of them; what matters is that thinking tokens are *reported separately*
    rather than hidden inside the completion count.
    """

    def __init__(self, model: str | None = None, api_key: str | None = None):
        from google import genai

        self.model = model or os.getenv("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL
        self.name = f"gemini:{self.model}"
        key = api_key or os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set (checked .env and environment)")
        self._client = genai.Client(api_key=key)

    def complete(self, system: str, user: str) -> LLMResult:
        from google.genai import types

        cfg = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=MAX_TOKENS,
        )
        t0 = time.perf_counter()
        resp, retries = with_backoff(lambda: self._client.models.generate_content(
            model=self.model, contents=user, config=cfg))
        latency_ms = (time.perf_counter() - t0) * 1000

        um = resp.usage_metadata
        text = resp.text or ""
        if not text.strip():
            fr = resp.candidates[0].finish_reason if resp.candidates else None
            text = f"[no text returned; finish_reason={fr}]"

        return LLMResult(
            text=text.strip(),
            prompt_tokens=um.prompt_token_count or 0,
            completion_tokens=um.candidates_token_count or 0,
            thinking_tokens=getattr(um, "thoughts_token_count", 0) or 0,
            cached_tokens=getattr(um, "cached_content_token_count", 0) or 0,
            model=self.model,
            latency_ms=latency_ms,
            retries=retries,
        )


class AnthropicLLM(LLM):
    def __init__(self, model: str | None = None, api_key: str | None = None):
        import anthropic

        self.model = model or os.getenv("ANTHROPIC_MODEL") or DEFAULT_ANTHROPIC_MODEL
        self.name = f"anthropic:{self.model}"
        # Zero-arg client resolves ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN /
        # an `ant auth login` profile, in that order.
        self._client = (anthropic.Anthropic(api_key=api_key) if api_key
                        else anthropic.Anthropic())

    def complete(self, system: str, user: str) -> LLMResult:
        t0 = time.perf_counter()
        resp, retries = with_backoff(lambda: self._client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
        ))
        latency_ms = (time.perf_counter() - t0) * 1000

        text = "".join(b.text for b in resp.content if b.type == "text")
        if resp.stop_reason == "refusal":
            text = text or "[model declined to answer]"

        return LLMResult(
            text=text.strip(),
            prompt_tokens=resp.usage.input_tokens,
            completion_tokens=resp.usage.output_tokens,
            model=self.model,
            latency_ms=latency_ms,
            retries=retries,
        )


class EchoLLM(LLM):
    """Offline stand-in for tests. Never used for the smoke test — an answer it
    produced would say nothing about whether the pipeline actually works."""

    name = "echo"

    def complete(self, system: str, user: str) -> LLMResult:
        n = user.count("[")
        return LLMResult(
            text=f"[echo] {n} chunks in prompt. [1]",
            prompt_tokens=len(system.split()) + len(user.split()),
            completion_tokens=8,
            model="echo",
            latency_ms=0.0,
        )


def get_llm(provider: str | None = None) -> LLM:
    """Factory. `provider` overrides the LLM_PROVIDER env var."""
    from dotenv import load_dotenv
    load_dotenv()

    provider = provider or os.getenv("LLM_PROVIDER", "gemini")
    if provider == "gemini":
        return GeminiLLM()
    if provider == "anthropic":
        return AnthropicLLM()
    if provider == "echo":
        return EchoLLM()
    raise ValueError(f"unknown LLM provider: {provider!r}")
