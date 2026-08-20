"""The spool drain — hook artifacts → the same floor path the scanner feeds.

Store-and-forward, ported from the proven production drain: a spool file is
removed ONLY after its content is durably in the lake + floor. Failure = the
file stays and is retried next tick. Outage = delay, never loss.

Filename contract (the proven one, full session id — never truncated):
    <session>.turn-<from_exclusive>-<through_inclusive>.jsonl    line-delta
    <session>.jsonl                                              final whole snapshot
Dot-prefixed files are invisible (atomic tmp+rename writers).

Reconciliation with the scanner (shared floor.ingest_state, keyed by session):
  - delta extends exactly (from == last_line_no)  -> apply
  - delta is entirely known (through <= last)     -> discard (already ingested)
  - delta has a gap (from > last)                 -> DEFER: leave in spool; the
    scanner reads the origin file and catches up; next tick discards the dup
  - whole snapshot: treated as origin-content; only lines past last are applied
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

from .paths import spool_dir
from .visit import get_state, ingest_chunk, split_complete_lines

_UUID = r"[0-9a-fA-F-]{36}"
TURN_DELTA_RE = re.compile(rf"^(?P<session>{_UUID})\.turn-(?P<frm>\d+)-(?P<through>\d+)\.jsonl$")
SNAPSHOT_RE = re.compile(rf"^(?P<session>{_UUID})\.jsonl$")

# lane -> the origin path a hook-captured session maps back to (for files.abs_path);
# the hook doesn't pass it, so the drain records the spool provenance instead.
_LANE_AGENT = {"cc_transcript": "cc"}


def _captured_at(p: Path) -> str:
    """Spool-file mtime — the moment the hook durably held the bytes.
    NEVER null (the measured invisible-rows lesson)."""
    return datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()


def drain_tick(lane: str = "cc_transcript") -> dict:
    """One drain pass over one lane's spool. Returns a census of outcomes."""
    agent = _LANE_AGENT.get(lane, lane.split("_")[0])
    sd = spool_dir(lane)
    if not sd.exists():
        return {"missing_spool_dir": 1}

    census: dict[str, int] = {}

    def bump(k: str) -> None:
        census[k] = census.get(k, 0) + 1

    for p in sorted(sd.iterdir()):  # sorted: deltas apply oldest-first
        if not p.is_file() or p.name.startswith("."):
            continue

        m = TURN_DELTA_RE.match(p.name)
        snap = SNAPSHOT_RE.match(p.name) if not m else None
        if not m and not snap:
            bump("unrecognized_left_in_spool")
            continue

        session = (m or snap).group("session")
        state = get_state(lane, session)
        last_ln = state["last_line_no"] if state else 0
        last_off = state["last_offset"] if state else 0
        gen = state["witness_gen"] if state else 1
        origin_path = f"spool://{lane}/{session}"  # hook lane provenance; scanner upgrades it

        raw = p.read_bytes()

        if m:  # line-delta
            frm = int(m.group("frm"))
            through = int(m.group("through"))
            if through <= last_ln:
                p.unlink()  # entirely known — content already in the floor
                bump("discarded_dup")
                continue
            if frm > last_ln:
                bump("deferred_gap")  # scanner will catch up from the origin
                continue
            # frm <= last_ln < through: skip the overlapping prefix lines
            lines, _ = split_complete_lines(raw)
            fresh = lines[last_ln - frm:]
            if not fresh:
                p.unlink()
                bump("discarded_empty")
                continue
            applied = ingest_chunk(
                lane=lane, agent=agent, session=session, origin_path=origin_path,
                chunk=b"".join(fresh), gen=gen, base_offset=last_off,
                base_line_no=last_ln, captured_at=_captured_at(p), source="hook-delta",
            )
            if applied:
                p.unlink()  # confirm-then-delete: store-and-forward
                bump("applied_delta")
            else:
                bump("torn_wait")
            continue

        # whole-session snapshot: apply only the unseen tail
        lines, _ = split_complete_lines(raw)
        if len(lines) <= last_ln:
            p.unlink()
            bump("discarded_snapshot_known")
            continue
        fresh = lines[last_ln:]
        applied = ingest_chunk(
            lane=lane, agent=agent, session=session, origin_path=origin_path,
            chunk=b"".join(fresh), gen=gen, base_offset=last_off,
            base_line_no=last_ln, captured_at=_captured_at(p), source="hook-final",
        )
        if applied:
            p.unlink()
            bump("applied_snapshot")
        else:
            bump("torn_wait")

    return census
