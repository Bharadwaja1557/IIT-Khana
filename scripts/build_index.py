"""Build the RAG vector index. Entry point: python -m scripts.build_index"""
from __future__ import annotations

import argparse

from rag.index import MODEL_NAME, META_PATH, VEC_PATH, build


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vectors", default=str(VEC_PATH))
    ap.add_argument("--meta", default=str(META_PATH))
    args = ap.parse_args(argv)

    from pathlib import Path
    n, elapsed = build(vec_path=Path(args.vectors), meta_path=Path(args.meta))
    print(f"embedded {n} chunks with {MODEL_NAME} in {elapsed:.1f}s")
    print(f"  vectors -> {args.vectors}")
    print(f"  metadata -> {args.meta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
