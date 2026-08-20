"""The visit algorithm + the shared chunk-ingest.

One code path feeds the floor from both directions:
  - scanner: origin file grew → consume new COMPLETE lines from origin
  - drain:   hook-spooled delta artifact → apply if it exactly extends the session

Invariants (preflight-proven):
  - lake/<lane>/<session>.jsonl is byte-identical prefix growth of the origin (P1)
  - only complete lines are consumed; a torn trailing line waits (newline-atomicity)
  - rewrite/truncation detected by prefix-sha → new GENERATION file, old kept
  - captured_at NOT NULL, ever (the 89/89 lesson)
  - DuckDB is touched open-write-close per batch (P2)
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import manifest
from .floor_db import write_conn
from .machine import machine_id
from .paths import lake_file


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def file_identity(lane: str, agent: str, abs_path: str) -> str:
    return _sha256(f"{machine_id()}:{agent}:{abs_path}".encode())[:32]


def _event_time(raw: bytes) -> str | None:
    """The line's OWN clock. CC / Codex / Pi all carry a top-level "timestamp" (measured)."""
    try:
        e = json.loads(raw)
        ts = e.get("timestamp")
        if isinstance(ts, str) and len(ts) >= 19:
            return ts
    except Exception:
        pass
    return None


def _decode(raw: bytes) -> tuple[str, bool]:
    """UTF-8 with honesty: DuckDB VARCHAR must be valid UTF-8; the lake object stays byte-exact."""
    try:
        return raw.decode("utf-8"), False
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace"), True


def split_complete_lines(chunk: bytes) -> tuple[list[bytes], bytes]:
    """Complete lines (keepends) + the torn trailing remainder (not consumed)."""
    if not chunk:
        return [], b""
    lines = chunk.splitlines(keepends=True)
    if lines and not lines[-1].endswith(b"\n"):
        return lines[:-1], lines[-1]
    return lines, b""


def get_state(lane: str, session: str) -> dict | None:
    with write_conn() as c:  # write_conn: state reads happen inside writer ticks
        row = c.execute(
            "SELECT file_id, witness_gen, last_size, last_offset, last_line_no, prefix_sha256 "
            "FROM floor.ingest_state s JOIN floor.files f USING (file_id) "
            "WHERE f.lane = ? AND f.session_hint = ?",
            [lane, session],
        ).fetchone()
    if not row:
        return None
    keys = ["file_id", "witness_gen", "last_size", "last_offset", "last_line_no", "prefix_sha256"]
    return dict(zip(keys, row))


def ingest_chunk(
    *,
    lane: str,
    agent: str,
    session: str,
    origin_path: str,
    chunk: bytes,
    gen: int,
    base_offset: int,
    base_line_no: int,
    captured_at: str,
    source: str,  # 'scan' | 'hook-delta' | 'hook-final'
) -> int:
    """Append complete lines from `chunk` to the lake + manifest + DuckDB. Returns lines applied.

    Order of durability (store-and-forward): lake append FIRST, manifest SECOND,
    DuckDB LAST (cache). A crash between steps re-applies idempotently: the lake
    append is guarded by the length check; DuckDB inserts are PK-idempotent.
    """
    lines, torn = split_complete_lines(chunk)
    if not lines:
        return 0
    body = b"".join(lines)

    lf = lake_file(lane, session, gen)
    lf.parent.mkdir(parents=True, exist_ok=True)
    cur = lf.stat().st_size if lf.exists() else 0
    if cur != base_offset:
        if cur >= base_offset + len(body):
            pass  # lake already holds these bytes (crash replay) — fall through to DB idempotent insert
        else:
            raise RuntimeError(
                f"lake integrity: {lf} is {cur} bytes, chunk expects base {base_offset} — refusing"
            )
    else:
        fd = os.open(lf, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, body)
        finally:
            os.close(fd)

    ev_first = _event_time(lines[0])
    manifest.append(
        {
            "lane": lane,
            "agent": agent,
            "session": session,
            "gen": gen,
            "byte_from": base_offset,
            "byte_to": base_offset + len(body),
            "line_from": base_line_no + 1,
            "line_to": base_line_no + len(lines),
            "chunk_sha256": _sha256(body),
            "captured_at": captured_at,
            "event_ts_first": ev_first,
            "machine_id": machine_id(),
            "origin_path": origin_path,
            "source": source,
        }
    )

    fid = file_identity(lane, agent, origin_path)
    rows = []
    off = base_offset
    for i, raw_nl in enumerate(lines):
        raw = raw_nl.rstrip(b"\n").rstrip(b"\r")
        text, replaced = _decode(raw)
        rows.append(
            (
                fid, gen, off, base_line_no + 1 + i, text, _sha256(raw), len(raw_nl),
                replaced, lane, agent, _event_time(raw), captured_at,
            )
        )
        off += len(raw_nl)

    new_size = base_offset + len(body)
    prefix_sha = _sha256_lake_prefix(lf, new_size)
    with write_conn() as c:
        c.execute(
            "INSERT INTO floor.files VALUES (?,?,?,?,?,?, now()) ON CONFLICT DO NOTHING",
            [fid, machine_id(), agent, lane, origin_path, session],
        )
        c.executemany(
            "INSERT INTO floor.raw_lines VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
            rows,
        )
        c.execute(
            "INSERT INTO floor.ingest_state VALUES (?,?,?,?,?,?, now()) "
            "ON CONFLICT (file_id) DO UPDATE SET witness_gen=excluded.witness_gen, "
            "last_size=excluded.last_size, last_offset=excluded.last_offset, "
            "last_line_no=excluded.last_line_no, prefix_sha256=excluded.prefix_sha256, updated_at=now()",
            [fid, gen, new_size, new_size, base_line_no + len(lines), prefix_sha],
        )
        c.execute(
            "INSERT INTO floor.witnesses VALUES (?, now(), ?, NULL, ?, NULL, ?)",
            [fid, new_size, "first" if base_offset == 0 else "append", len(lines)],
        )
    return len(lines)


def _sha256_lake_prefix(lf: Path, upto: int) -> str:
    h = hashlib.sha256()
    with open(lf, "rb") as f:
        remaining = upto
        while remaining > 0:
            b = f.read(min(1 << 20, remaining))
            if not b:
                break
            h.update(b)
            remaining -= len(b)
    return h.hexdigest()


def visit_origin(lane: str, agent: str, origin: Path, session: str) -> str:
    """One scanner visit to one origin file. Returns the growth verdict."""
    st = origin.stat()
    state = get_state(lane, session)
    captured_at = datetime.now(timezone.utc).isoformat()

    if state is None:
        with open(origin, "rb") as f:
            chunk = f.read()
        ingest_chunk(
            lane=lane, agent=agent, session=session, origin_path=str(origin),
            chunk=chunk, gen=1, base_offset=0, base_line_no=0,
            captured_at=captured_at, source="scan",
        )
        return "first"

    gen, last_off, last_ln = state["witness_gen"], state["last_offset"], state["last_line_no"]

    if st.st_size == state["last_size"]:
        return "unchanged"

    # prefix check: did the bytes we already hold still hash the same in the ORIGIN?
    prefix_ok = False
    if st.st_size >= last_off:
        h = hashlib.sha256()
        with open(origin, "rb") as f:
            remaining = last_off
            while remaining > 0:
                b = f.read(min(1 << 20, remaining))
                if not b:
                    break
                h.update(b)
                remaining -= len(b)
        prefix_ok = h.hexdigest() == state["prefix_sha256"]

    if st.st_size < last_off or not prefix_ok:
        # rewrite/truncation (the proven silent-stop defect, fixed): new generation, keep the old
        new_gen = gen + 1
        with open(origin, "rb") as f:
            chunk = f.read()
        ingest_chunk(
            lane=lane, agent=agent, session=session, origin_path=str(origin),
            chunk=chunk, gen=new_gen, base_offset=0, base_line_no=0,
            captured_at=captured_at, source="scan",
        )
        return "rewrite"

    with open(origin, "rb") as f:
        f.seek(last_off)
        chunk = f.read()
    n = ingest_chunk(
        lane=lane, agent=agent, session=session, origin_path=str(origin),
        chunk=chunk, gen=gen, base_offset=last_off, base_line_no=last_ln,
        captured_at=captured_at, source="scan",
    )
    return "append" if n else "torn-wait"
