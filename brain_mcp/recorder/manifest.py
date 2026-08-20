"""Append-only manifest — one versioned line per chunk applied to the lake.

The manifest + lake ARE the floor of record; everything in DuckDB re-derives
from them. Lines carry {"v":1,...} from day one (cross-check M10).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .paths import manifest_path

MANIFEST_V = 1


def append(entry: dict) -> None:
    entry = {"v": MANIFEST_V, **entry}
    p = manifest_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, separators=(",", ":"), default=str) + "\n"
    # O_APPEND single write: atomic enough for one-writer-per-tick lines under PIPE_BUF
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line.encode())
    finally:
        os.close(fd)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
