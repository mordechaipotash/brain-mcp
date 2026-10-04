"""The watch-lane scanner — a poll-scan, deliberately not an event listener.

A dead FSEvents/inotify listener is the canonical present-but-inactive failure:
it looks identical to a quiet day. A scheduler-run poll (60s) has no daemon
state to lose — its entire state is (glob result, floor.ingest_state), and the
scheduler restarts it every tick by construction. (Spec §2; P4 made Codex
scanner-only; P5 made deltas primary for hookless lanes.)
"""

from __future__ import annotations

import glob
import json
import re
from pathlib import Path

from .floor_db import read_conn
from .paths import glob_root
from .visit import visit_blob, visit_origin

# session-id extraction per lane, from the filename (all three dialects put it there)
_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_SESSION_PATTERNS = {
    "cc_transcript": re.compile(rf"({_UUID})\.jsonl$"),
    "codex_rollout": re.compile(rf"rollout-.*?({_UUID})\.jsonl$"),
    "pi_session": re.compile(rf"({_UUID})\.jsonl$"),
}
# cc_sessiondir keys a file by its path under the projects root: <project>/<session>/<rest>.
# The second component must be a session uuid — that is what makes it a session's folder.
_SESSIONDIR_RE = re.compile(rf"^[^/]+/{_UUID}/.+$")


def session_from_filename(lane: str, path: Path) -> str | None:
    pat = _SESSION_PATTERNS.get(lane)
    if pat:
        m = pat.search(path.name)
        if m:
            return m.group(1)
    return None


def sessiondir_key(path: Path, root: Path) -> tuple[str, str] | None:
    """(key, mode) for a file inside a CC session folder, or None.

    .jsonl files (subagent and workflow transcripts) are captured line by line and keyed
    without the extension, exactly like a main transcript; anything else (.meta.json,
    tool-results/*) is captured whole and keeps its name."""
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        return None
    if not _SESSIONDIR_RE.match(rel):
        return None
    if rel.endswith(".jsonl"):
        return rel[: -len(".jsonl")], "lines"
    return rel, "blob"


def _lanes() -> list[dict]:
    with read_conn() as c:
        rows = c.execute(
            "SELECT lane, agent, mode, root_glob FROM floor.lanes WHERE enabled"
        ).fetchall()
    return [dict(zip(["lane", "agent", "mode", "root_glob"], r)) for r in rows]


def scan_tick(only_lane: str | None = None) -> dict:
    """One scanner pass over every enabled lane. Returns a per-lane verdict census.

    Hook-mode lanes are scanned too — the scanner is the hook's backstop (shared
    ingest_state means they never double-ingest; the scanner only picks up what
    the hook missed: disabled hooks, subagent transcripts, missed Stops).
    """
    census: dict[str, dict] = {}
    for lane_row in _lanes():
        lane = lane_row["lane"]
        if only_lane and lane != only_lane:
            continue
        agent = lane_row["agent"]
        root = glob_root(lane_row["root_glob"])
        # One read per lane instead of one DuckDB open per file: a visit's first check is
        # "size == last_size -> unchanged", so answer that here. Measured 2026-10-04: 5.3 ms
        # per unchanged file, ~16 s a tick for one machine's 3,046 session-folder files.
        with read_conn() as c:
            last_size = dict(c.execute(
                "SELECT f.session_hint, s.last_size FROM floor.ingest_state s "
                "JOIN floor.files f USING (file_id) WHERE f.lane = ?", [lane]).fetchall())
        counts: dict[str, int] = {}
        skipped_no_session = 0
        for f in glob.glob(str(Path(lane_row["root_glob"]).expanduser()), recursive=True):
            p = Path(f)
            if not p.is_file() or p.name.startswith("."):
                continue
            mode = "lines"
            if lane == "cc_sessiondir":
                keyed = sessiondir_key(p, root)
                session, mode = keyed if keyed else (None, "lines")
            else:
                session = session_from_filename(lane, p)
            if session is None:
                skipped_no_session += 1
                continue
            if last_size.get(session) == p.stat().st_size:
                counts["unchanged"] = counts.get("unchanged", 0) + 1
                continue
            try:
                if mode == "blob":
                    verdict = visit_blob(lane, agent, p, session)
                else:
                    verdict = visit_origin(lane, agent, p, session)
            except Exception as e:  # one bad file must not block the lane
                verdict = "error"
                counts.setdefault("_last_error", 0)
                census.setdefault("_errors", {}).setdefault(lane, []).append(
                    f"{p.name}: {type(e).__name__}: {e}"
                )
            counts[verdict] = counts.get(verdict, 0) + 1
        if skipped_no_session:
            counts["skipped_no_session_id"] = skipped_no_session
        census[lane] = counts
    return census


def main() -> None:  # pragma: no cover — CLI shim
    from .floor_db import init_db
    from .paths import ensure_layout

    ensure_layout([l["lane"] for l in [] ] or ["cc_transcript", "codex_rollout", "pi_session"])
    init_db()
    print(json.dumps(scan_tick(), indent=2, default=str))


if __name__ == "__main__":  # pragma: no cover
    main()
