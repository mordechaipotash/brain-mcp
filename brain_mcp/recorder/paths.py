"""BRAIN_HOME layout — one home, one table of truth (spec §1/§2).

~/.brain/
  spool/<lane>/        hooks + watchers write here (atomic tmp+rename; dot-prefix invisible)
  lake/<lane>/         THE FLOOR OF RECORD: <session>.jsonl append-only session files
                       (rewrites open a new generation: <session>.g2.jsonl; old kept)
  manifest/manifest.jsonl   append-only, versioned lines ({"v":1,...})
  offsets/<lane>/<session>  line-count + prefix-fingerprint (rewrite detection)
  health/              side-effect heartbeats (mtimes are the signal, never self-report)
  brain.duckdb         CACHE — fully re-derivable from lake/ + manifest/
"""

from __future__ import annotations

import os
from pathlib import Path


def brain_home() -> Path:
    return Path(os.environ.get("BRAIN_HOME", "~/.brain")).expanduser()


def spool_dir(lane: str) -> Path:
    return brain_home() / "spool" / lane


def lake_dir(lane: str) -> Path:
    return brain_home() / "lake" / lane


def manifest_path() -> Path:
    return brain_home() / "manifest" / "manifest.jsonl"


def offsets_dir(lane: str) -> Path:
    return brain_home() / "offsets" / lane


def health_dir() -> Path:
    return brain_home() / "health"


def db_path() -> Path:
    return brain_home() / "brain.duckdb"


def lake_file(lane: str, session: str, gen: int = 1) -> Path:
    """Generation 1 is bare <session>.jsonl; later generations are <session>.gN.jsonl."""
    name = f"{session}.jsonl" if gen == 1 else f"{session}.g{gen}.jsonl"
    return lake_dir(lane) / name


def ensure_layout(lanes: list[str] | None = None) -> Path:
    home = brain_home()
    for d in (home, home / "manifest", health_dir()):
        d.mkdir(parents=True, exist_ok=True)
    # The floor is private by default (spec §6 M3).
    os.chmod(home, 0o700)
    for lane in lanes or []:
        for d in (spool_dir(lane), lake_dir(lane), offsets_dir(lane)):
            d.mkdir(parents=True, exist_ok=True)
    return home
