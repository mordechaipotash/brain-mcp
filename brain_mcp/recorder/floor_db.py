"""The DuckDB cache — schema + open-write-close discipline.

P2 (preflight, measured 2026-08-20): a read_only connection cannot even OPEN
the file while a writer holds it. So: writers open, write one batch, close.
Readers open read_only per query with retry. NOTHING holds a connection.

The DB is a cache: every table here is re-derivable from lake/ + manifest/.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path

import duckdb

from .paths import db_path

SCHEMA = """
CREATE SCHEMA IF NOT EXISTS floor;

CREATE TABLE IF NOT EXISTS floor.lanes (
  lane       TEXT PRIMARY KEY,     -- 'cc_transcript' | 'codex_rollout' | 'pi_session' ...
  agent      TEXT NOT NULL,        -- 'cc' | 'codex' | 'pi'
  mode       TEXT NOT NULL CHECK (mode IN ('hook','watch')),
  root_glob  TEXT NOT NULL,
  enabled    BOOLEAN NOT NULL DEFAULT true
);

-- one row per ORIGIN file ever seen (identity carries machine_id: cross-check M4)
CREATE TABLE IF NOT EXISTS floor.files (
  file_id      TEXT PRIMARY KEY,   -- sha256(machine_id:agent:realpath)[:32]
  machine_id   TEXT NOT NULL,
  agent        TEXT NOT NULL,
  lane         TEXT NOT NULL,
  abs_path     TEXT NOT NULL,
  session_hint TEXT,               -- uuid regex'd from filename
  first_seen   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- append-only observation log (gravity's witness idea, per visit)
CREATE TABLE IF NOT EXISTS floor.witnesses (
  file_id      TEXT NOT NULL,
  observed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  size_bytes   BIGINT NOT NULL,
  mtime        TIMESTAMPTZ,
  growth       TEXT NOT NULL,      -- 'first' | 'append' | 'rewrite' | 'truncated' | 'unchanged'
  prefix_ok    BOOLEAN,
  new_lines    INTEGER NOT NULL DEFAULT 0
);

-- per-line rows. Duplicates preserved; dedup is derived-layer business (C3 resolution).
CREATE TABLE IF NOT EXISTS floor.raw_lines (
  file_id       TEXT NOT NULL,
  witness_gen   SMALLINT NOT NULL DEFAULT 1,   -- bumped on rewrite; OLD GENERATIONS KEPT
  byte_offset   BIGINT NOT NULL,
  line_no       INTEGER NOT NULL,              -- 1-based within (file, gen) — the citation line number
  raw_line      TEXT NOT NULL,                 -- utf-8; lake object is byte-exact if this was replaced
  line_sha256   TEXT NOT NULL,                 -- sha256 of ORIGINAL line bytes (no trailing newline)
  n_bytes       INTEGER NOT NULL,              -- original byte length incl. newline
  utf8_replaced BOOLEAN NOT NULL DEFAULT false,
  lane          TEXT NOT NULL,
  agent         TEXT NOT NULL,
  event_time    TIMESTAMPTZ,                   -- the line's OWN clock; nullable (not every line has one)
  captured_at   TIMESTAMPTZ NOT NULL,          -- NEVER null (the 89/89 lesson)
  PRIMARY KEY (file_id, witness_gen, byte_offset)
);
CREATE INDEX IF NOT EXISTS rl_evt  ON floor.raw_lines(event_time);
CREATE INDEX IF NOT EXISTS rl_lane ON floor.raw_lines(lane);

-- the sync-state v1 defined and never used, made real
CREATE TABLE IF NOT EXISTS floor.ingest_state (
  file_id       TEXT PRIMARY KEY,
  witness_gen   SMALLINT NOT NULL DEFAULT 1,
  last_size     BIGINT NOT NULL,
  last_offset   BIGINT NOT NULL,     -- byte offset AFTER the last complete consumed line
  last_line_no  INTEGER NOT NULL,
  prefix_sha256 TEXT NOT NULL,       -- sha256 of bytes [0, last_offset)
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

DEFAULT_LANES = [
    # (lane, agent, mode, root_glob) — Codex is scanner-only (P4); CC scanner is the hook backstop.
    ("cc_transcript", "cc", "hook", "~/.claude/projects/**/*.jsonl"),
    ("codex_rollout", "codex", "watch", "~/.codex/sessions/**/*.jsonl"),
    ("pi_session", "pi", "watch", "~/.pi/agent/sessions/**/*.jsonl"),
]


@contextmanager
def write_conn(path: Path | None = None):
    """Open → write one batch → close. Retry on lock contention (P2b measured collisions)."""
    p = str(path or db_path())
    last = None
    for attempt in range(6):
        try:
            conn = duckdb.connect(p)
            break
        except duckdb.IOException as e:  # lock held by a reader/writer
            last = e
            time.sleep(0.2 * (attempt + 1))
    else:
        raise last
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def read_conn(path: Path | None = None):
    p = str(path or db_path())
    last = None
    for attempt in range(6):
        try:
            conn = duckdb.connect(p, read_only=True)
            break
        except duckdb.IOException as e:
            last = e
            time.sleep(0.2 * (attempt + 1))
    else:
        raise last
    try:
        yield conn
    finally:
        conn.close()


def init_db(path: Path | None = None) -> None:
    with write_conn(path) as c:
        c.execute(SCHEMA)
        for lane, agent, mode, glob_ in DEFAULT_LANES:
            c.execute(
                "INSERT INTO floor.lanes VALUES (?,?,?,?,true) ON CONFLICT DO NOTHING",
                [lane, agent, mode, glob_],
            )
