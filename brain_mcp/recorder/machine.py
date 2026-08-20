"""Stable machine identity (spec §2 / cross-check M4).

Two machines pushing to one mode-2 floor must never collide on
file identity: machine_id rides in the manifest and in file_id.
"""

from __future__ import annotations

import hashlib
import socket
import uuid

from .paths import brain_home

_MACHINE_FILE = "machine-id"


def machine_id() -> str:
    """First call mints and persists; later calls read. 12 hex chars."""
    p = brain_home() / _MACHINE_FILE
    if p.exists():
        return p.read_text().strip()
    raw = f"{socket.gethostname()}:{uuid.uuid4()}"
    mid = hashlib.sha256(raw.encode()).hexdigest()[:12]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(mid + "\n")
    return mid
