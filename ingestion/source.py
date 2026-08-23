"""Read the cached campusmess.in responses.

Phase 1 does not touch the network. Everything comes from the recon cache
written in Phase 0. The cache lives under `.notes/recon/` by default, which is
gitignored — so a fresh clone must re-run the Phase 0 fetch (or point
RAW_CACHE_DIR elsewhere) before ingesting. Recorded in DECISIONS.md D18.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

DEFAULT_CACHE = Path(__file__).resolve().parent.parent / ".notes" / "recon"

_HALLS = "campusmess.in__api_halls.body"
_WEEKLY = re.compile(r"campusmess\.in__api_halls_(\d+)_weekly\.body$")


def cache_dir() -> Path:
    return Path(os.environ.get("RAW_CACHE_DIR") or DEFAULT_CACHE)


def _envelope(path: Path):
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict) or "data" not in payload:
        raise ValueError(f"{path.name}: not a campusmess API envelope")
    return payload["data"]


def load_halls(root: Path | None = None) -> list[dict]:
    root = root or cache_dir()
    path = root / _HALLS
    if not path.exists():
        raise FileNotFoundError(
            f"missing {path}. Phase 0 recon cache not found; see DECISIONS.md D18."
        )
    return _envelope(path)


def load_weekly(root: Path | None = None) -> dict[int, list[dict]]:
    """hall_id -> its 21 upstream menu rows."""
    root = root or cache_dir()
    out: dict[int, list[dict]] = {}
    for path in sorted(root.glob("campusmess.in__api_halls_*_weekly.body")):
        m = _WEEKLY.search(path.name)
        if m:
            out[int(m.group(1))] = _envelope(path)
    if not out:
        raise FileNotFoundError(f"no weekly menu files in {root}")
    return out
