"""Embed chunks and search them. Persisted next to the SQLite DB.

Storage: a plain `.npy` float32 matrix plus a `.json` sidecar of chunk metadata,
both in `db/`. See DECISIONS.md D23 for why this is not FAISS or sqlite-vec.

Model: BAAI/bge-small-en-v1.5 via fastembed (ONNX runtime, no torch).
Vectors come back L2-normalised, so cosine similarity IS the dot product.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .chunk import Chunk, build_chunks

MODEL_NAME = "BAAI/bge-small-en-v1.5"

# bge-v1.5 is trained asymmetrically: queries get an instruction prefix,
# documents do not. Skipping this measurably degrades retrieval, and a fair
# baseline uses the model the way its authors intended.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

VEC_PATH = Path("db/chunks.npy")
META_PATH = Path("db/chunks.meta.json")

_model = None


def get_model():
    """Lazy — importing fastembed and loading ONNX costs ~6s."""
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding(MODEL_NAME)
    return _model


def embed_documents(texts: list[str]) -> np.ndarray:
    return np.asarray(list(get_model().embed(texts)), dtype=np.float32)


def embed_query(text: str) -> np.ndarray:
    v = list(get_model().embed([QUERY_PREFIX + text]))[0]
    return np.asarray(v, dtype=np.float32)


def build(db_path: Path | None = None,
          vec_path: Path = VEC_PATH,
          meta_path: Path = META_PATH) -> tuple[int, float]:
    chunks = build_chunks(db_path) if db_path else build_chunks()
    t0 = time.perf_counter()
    vecs = embed_documents([c.text for c in chunks])
    elapsed = time.perf_counter() - t0

    vec_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(vec_path, vecs)
    meta_path.write_text(json.dumps(
        {"model": MODEL_NAME, "dim": int(vecs.shape[1]),
         "chunks": [c.as_dict() for c in chunks]}, indent=2))
    return len(chunks), elapsed


@dataclass(frozen=True)
class Hit:
    rank: int
    score: float
    chunk: Chunk


class Index:
    """Exact flat cosine search over an in-memory matrix.

    At n=294 an exact scan is ~0.1 ms and is strictly more accurate than any
    approximate index. See D23.
    """

    def __init__(self, vecs: np.ndarray, chunks: list[Chunk], model: str):
        self.vecs = vecs
        self.chunks = chunks
        self.model = model

    @classmethod
    def load(cls, vec_path: Path = VEC_PATH, meta_path: Path = META_PATH) -> "Index":
        if not vec_path.exists() or not meta_path.exists():
            raise FileNotFoundError(
                f"index not built — run `python -m scripts.build_index` "
                f"(missing {vec_path if not vec_path.exists() else meta_path})")
        meta = json.loads(meta_path.read_text())
        chunks = [Chunk(**c) for c in meta["chunks"]]
        return cls(np.load(vec_path), chunks, meta["model"])

    def search(self, query: str, k: int = 5) -> list[Hit]:
        q = embed_query(query)
        scores = self.vecs @ q                      # both sides L2-normalised
        k = min(k, len(self.chunks))
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        return [Hit(rank=i + 1, score=float(scores[j]), chunk=self.chunks[j])
                for i, j in enumerate(top)]
