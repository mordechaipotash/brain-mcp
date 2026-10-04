"""BRAIN_HOME layout — one home, one table of truth (spec §1/§2).

~/.brain/
  spool/<lane>/        hooks + watchers write here (atomic tmp+rename; dot-prefix invisible)
  lake/<lane>/         THE FLOOR OF RECORD: <session>.jsonl append-only session files
                       (rewrites open a new generation: <session>.g2.jsonl; old kept)
  manifest/manifest.jsonl   append-only, versioned lines ({"v":1,...})
  offsets/<lane>/<session>  line-count + prefix-fingerprint (rewrite detection)
  imports/<source>/    manufactured origin for sources with no local files
                       (a ChatGPT export becomes <conversation>.jsonl here)
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


def imports_dir(source: str) -> Path:
    """Origin for sources with no local files of their own (e.g. a ChatGPT export)."""
    return brain_home() / "imports" / source


def health_dir() -> Path:
    return brain_home() / "health"


def db_path() -> Path:
    return brain_home() / "brain.duckdb"


def lake_file(lane: str, session: str, gen: int = 1) -> Path:
    """Generation 1 is bare <session>.jsonl; later generations are <session>.gN.jsonl.

    `session` may be a relative key with slashes (the cc_sessiondir lane keys a file by
    its path under ~/.claude/projects), so the lake mirrors the origin's layout."""
    name = f"{session}.jsonl" if gen == 1 else f"{session}.g{gen}.jsonl"
    return lake_dir(lane) / name


def lake_blob(lane: str, key: str, gen: int = 1) -> Path:
    """A whole-file capture (not line-oriented): the key keeps its own extension, and a
    later generation is inserted before it — tool-results/x.txt -> tool-results/x.g2.txt."""
    p = Path(key)
    name = p.name if gen == 1 else f"{p.stem}.g{gen}{p.suffix}"
    return lake_dir(lane) / p.parent / name


def glob_root(root_glob: str) -> Path:
    """The directory every match of a lane glob is relative to: the last whole directory
    before the first wildcard (`projects/*/…` and `projects/-Users-x*/…` both -> projects)."""
    head = root_glob.split("*")[0]
    if not head.endswith("/"):
        head = head.rsplit("/", 1)[0] + "/"
    return Path(head.rstrip("/") or "/").expanduser()


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
