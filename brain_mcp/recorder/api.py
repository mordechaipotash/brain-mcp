"""The seven tool bodies — pure functions, no MCP dependency (server.py wraps them).

The response contract (spec §4 MUSTs):
  M1 cite-or-abstain: content rows carry {file, lines, sha256, ...}; zero hits ->
     {"abstained": true, "reason", "coverage", "hint"} — never a bare [].
  M2 every response carries measured_at + instrument.
  M3 unknown is never rendered as healthy; a reason is mandatory.
  M4 abstention states its coverage (lanes + dates + rows) and unmeasured lanes.
  M9 a zero names its scope.
  M10 read-only floor; backup writes only OUTSIDE the floor.
  M11 the search response reserves `attested` (absent until v2.1).
"""

from __future__ import annotations

import glob as _glob
import hashlib
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import derived
from .floor_db import read_conn
from .paths import brain_home, lake_dir, lake_file, manifest_path, spool_dir


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _envelope(instrument: str, unmeasured: list[str] | None = None) -> dict:
    return {"measured_at": _now(), "instrument": instrument, "unmeasured": unmeasured or []}


def _lanes() -> list[dict]:
    with read_conn() as c:
        rows = c.execute("SELECT lane, agent, mode, root_glob, enabled FROM floor.lanes").fetchall()
    return [dict(zip(["lane", "agent", "mode", "root_glob", "enabled"], r)) for r in rows]


_AGENT_LANE = {"claude-code": "cc_transcript", "codex": "codex_rollout", "pi": "pi_session"}


def _lake_rel(agent: str, session: str, gen: int) -> str:
    lane = _AGENT_LANE.get(agent, agent)
    name = f"{session}.jsonl" if gen == 1 else f"{session}.g{gen}.jsonl"
    return f"lake/{lane}/{name}"


def _span(path: Path, a: int, b: int) -> bytes:
    lines = path.read_bytes().splitlines(keepends=True)
    return b"".join(lines[a - 1:b])


def _citation(row: dict) -> dict:
    if not row.get("witness_gen"):  # v1-migrated rows carry no floor backing
        return {"v1_derived": True, "note": "derived from v1 parquet — raw bytes not held",
                "agent": row["agent"], "session_id": row["session_id"],
                "ts": str(row["event_time"]) if row["event_time"] else None, "role": row["role"]}
    rel = _lake_rel(row["agent"], row["session_id"], row["witness_gen"])
    p = brain_home() / rel
    a = b = row["line_no"]
    sha = hashlib.sha256(_span(p, a, b)).hexdigest() if p.exists() else None
    return {
        "file": rel, "lines": [a, b], "sha256": sha,
        "agent": row["agent"], "session_id": row["session_id"],
        "ts": str(row["event_time"]) if row["event_time"] else None, "role": row["role"],
    }


def _coverage() -> dict:
    with read_conn() as c:
        rows = c.execute(
            "SELECT agent, count(*), CAST(min(event_time) AS VARCHAR), CAST(max(event_time) AS VARCHAR) "
            "FROM derived.messages GROUP BY agent"
        ).fetchall()
        total = c.execute("SELECT count(*) FROM derived.messages").fetchone()[0]
    return {
        "lanes_searched": [r[0] for r in rows],
        "time_range": [str(min((r[2] for r in rows if r[2]), default=None)),
                       str(max((r[3] for r in rows if r[3]), default=None))] if rows else None,
        "rows": total,
    }


def _unwatched() -> list[str]:
    known = {l["lane"] for l in _lanes() if l["enabled"]}
    out = []
    probes = {"cc_transcript": "~/.claude/projects", "codex_rollout": "~/.codex/sessions",
              "pi_session": "~/.pi/agent/sessions", "cursor": "~/.cursor"}
    for lane, root in probes.items():
        if lane not in known and Path(root).expanduser().exists():
            out.append(f"lane '{lane}': present on disk but not watched — this response says nothing about it")
    return out


# ── 1. search ─────────────────────────────────────────────────────────────────

def search(query: str, agent: str | None = None, role: str | None = None,
           limit: int = 10, min_rank: float = 0.0, order: str = "rank") -> dict:
    hits = derived.search_fts(query, limit=limit, min_rank=min_rank, agent=agent, role=role)
    if order == "time_asc":
        hits.sort(key=lambda h: str(h["event_time"]))
    elif order == "time_desc":
        hits.sort(key=lambda h: str(h["event_time"]), reverse=True)
    cov = _coverage()
    env = _envelope(f"duckdb fts_bm25 over derived.messages ({cov['rows']} rows)", _unwatched())
    if not hits:
        return {**env, "abstained": True,
                "reason": f"no hit scored >= min_rank {min_rank} under fts_bm25 for query {query!r}",
                "coverage": cov,
                "hint": "absence here is evidence only about the lanes and dates in coverage; "
                        "try other keywords or widen the date range", "hits": []}
    out = []
    for h in hits:
        text = h["text"]
        excerpt = text if len(text) <= 600 else text[:600] + f"… (excerpt: 600 of {len(text)} chars — full via brain_get)"
        out.append({"excerpt": excerpt, "rank": round(h["rank"], 3), "citation": _citation(h)})
    return {**env, "abstained": False, "ranker": "fts_bm25", "hits": out, "coverage": cov}


# ── 2. get ────────────────────────────────────────────────────────────────────

def get(file: str, lines: list[int], expect_sha256: str | None = None, context: int = 0) -> dict:
    p = brain_home() / file
    env = _envelope(f"read {file} lines {lines[0]}-{lines[1]} + sha256")
    if not p.exists() or ".." in file or not file.startswith("lake/"):
        return {**env, "error": f"no such floor file: {file}", "verified": False}
    all_lines = p.read_bytes().splitlines(keepends=True)
    a, b = max(1, lines[0]), min(len(all_lines), lines[1])
    span = b"".join(all_lines[a - 1:b])
    sha = hashlib.sha256(span).hexdigest()
    verified = (sha == expect_sha256) if expect_sha256 else "no expectation supplied"
    resp = {**env,
            "lines": [{"n": i, "raw": all_lines[i - 1].decode("utf-8", "replace").rstrip("\n")}
                       for i in range(a, b + 1)],
            "sha256_of_span": sha, "verified": verified}
    if expect_sha256 and sha != expect_sha256:
        resp["warning"] = ("cited bytes differ from floor — file changed since indexing; "
                          "reindex or investigate. Reported, never repaired.")
    if context:
        resp["context_before"] = [{"n": i, "raw": all_lines[i - 1].decode("utf-8", "replace").rstrip("\n")}
                                   for i in range(max(1, a - context), a)]
        resp["context_after"] = [{"n": i, "raw": all_lines[i - 1].decode("utf-8", "replace").rstrip("\n")}
                                  for i in range(b + 1, min(len(all_lines), b + context) + 1)]
    return resp


# ── 3. recent ─────────────────────────────────────────────────────────────────

def recent(hours: int = 24, agent: str | None = None, role: str | None = None,
           limit: int = 20) -> dict:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    where, params = ["event_time >= ?"], [since]
    if agent:
        where.append("agent = ?"); params.append(agent)
    if role:
        where.append("role = ?"); params.append(role)
    with read_conn() as c:
        rows = c.execute(
            f"SELECT msg_id, agent, session_id, role, model, text, CAST(event_time AS VARCHAR), CAST(captured_at AS VARCHAR), "
            f"file_id, witness_gen, line_no FROM derived.messages "
            f"WHERE {' AND '.join(where)} ORDER BY event_time DESC LIMIT {int(limit)}",
            params).fetchall()
        cols = ["msg_id","agent","session_id","role","model","text","event_time","captured_at",
                "file_id","witness_gen","line_no"]
    hits = [dict(zip(cols, r)) for r in rows]
    cov = _coverage()
    env = _envelope(f"derived.messages where event_time >= now()-{hours}h", _unwatched())
    if not hits:
        return {**env, "hits": [], "ranker": "time_desc",
                "note": f"0 events in the last {hours}h across lanes {cov['lanes_searched']} — "
                        f"coverage, not silence", "coverage": cov}
    return {**env, "ranker": "time_desc", "coverage": cov,
            "hits": [{"excerpt": h["text"][:600], "citation": _citation(h)} for h in hits]}


# ── 4. sessions ───────────────────────────────────────────────────────────────

def sessions(date: str | None = None, agent: str | None = None, limit: int = 50) -> dict:
    where, params = [], []
    if date:
        where.append("CAST(event_time AS DATE) = ?"); params.append(date)
    if agent:
        where.append("agent = ?"); params.append(agent)
    w = ("WHERE " + " AND ".join(where)) if where else ""
    with read_conn() as c:
        rows = c.execute(
            f"SELECT agent, session_id, witness_gen, CAST(min(event_time) AS VARCHAR), CAST(max(event_time) AS VARCHAR), "
            f"count(*), sum(CASE WHEN role='user' THEN 1 ELSE 0 END) "
            f"FROM derived.messages {w} GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT {int(limit)}",
            params).fetchall()
    env = _envelope(f"derived.messages grouped by session{' for ' + date if date else ''}")
    return {**env, "sessions": [
        {"agent": r[0], "session_id": r[1],
         "file": _lake_rel(r[0], r[1], r[2]),
         "first_ts": str(r[3]), "last_ts": str(r[4]),
         "n_messages": r[5], "n_user_turns": r[6]} for r in rows],
        "note": None if rows else "0 sessions matched — scope: derived.messages, filters as given"}


# ── 5. health ─────────────────────────────────────────────────────────────────

def health() -> dict:
    lanes_out = []
    counts = {"fresh": 0, "stale": 0, "unknown": 0, "disabled": 0}
    grace = 300  # 5 min: 2 drain cadences + slack
    for l in _lanes():
        entry = {"lane": l["lane"], "mode": l["mode"]}
        if not l["enabled"]:
            entry.update(verdict="disabled", reason="lane disabled in floor.lanes")
            counts["disabled"] += 1
            lanes_out.append(entry)
            continue
        root = l["root_glob"].split("*")[0].rstrip("/")
        rootp = Path(root).expanduser()
        origin_newest = None
        if rootp.exists():
            mts = [Path(f).stat().st_mtime for f in _glob.glob(
                str(Path(l["root_glob"]).expanduser()), recursive=True)[:5000]]
            if mts:
                origin_newest = datetime.fromtimestamp(max(mts), tz=timezone.utc)
        with read_conn() as c:
            floor_last = c.execute(
                "SELECT CAST(max(captured_at) AS VARCHAR) FROM floor.raw_lines WHERE lane = ?", [l["lane"]]
            ).fetchone()[0]
        entry["origin_path"] = root
        entry["origin_newest"] = str(origin_newest) if origin_newest else None
        entry["floor_last_captured"] = str(floor_last) if floor_last else None
        entry["instrument"] = f"stat mtimes over {l['root_glob']} vs max(captured_at) lane={l['lane']}"
        if origin_newest is None:
            entry.update(verdict="unknown",
                         reason="origin path empty or unobservable — unmeasured, not healthy")
            counts["unknown"] += 1
        elif floor_last is None:
            entry.update(verdict="stale", staleness_seconds=None,
                         reason="origin has content; floor has never captured this lane")
            counts["stale"] += 1
        else:
            fl = datetime.fromisoformat(str(floor_last))  # VARCHAR from SQL (pytz-free path)
            if fl.tzinfo is None:
                fl = fl.replace(tzinfo=timezone.utc)
            gap = (origin_newest - fl).total_seconds()
            entry["staleness_seconds"] = max(0, int(gap))
            if gap <= grace:
                entry["verdict"] = "fresh"; counts["fresh"] += 1
            else:
                entry["verdict"] = "stale"; counts["stale"] += 1
        lanes_out.append(entry)
    env = _envelope("origin-vs-floor per lane; 'unknown' means unmeasured, not healthy, "
                    "and no summary relabels it", _unwatched())
    return {**env, "lanes": lanes_out, "summary": counts}


# ── 6. capture_status ─────────────────────────────────────────────────────────

def capture_status() -> dict:
    home = brain_home()
    spool_stats = {}
    for l in _lanes():
        sd = spool_dir(l["lane"])
        files = [f for f in sd.glob("*.jsonl")] if sd.exists() else []
        oldest = min((f.stat().st_mtime for f in files), default=None)
        spool_stats[l["lane"]] = {
            "depth_files": len(files),
            "oldest_age_seconds": int(datetime.now(timezone.utc).timestamp() - oldest) if oldest else None,
        }
    hb = {}
    hdir = home / "health"
    if hdir.exists():
        for f in hdir.iterdir():
            age = int(datetime.now(timezone.utc).timestamp() - f.stat().st_mtime)
            hb[f.name] = {"last_run_age_seconds": age}
    lake_files = sum(1 for _ in (home / "lake").rglob("*.jsonl")) if (home / "lake").exists() else 0
    env = _envelope("spool globs + health/ heartbeat file mtimes (side effects, never self-report)")
    return {**env, "spool": spool_stats, "heartbeats": hb or {"note": "no heartbeat files — machinery has never run, or hooks not installed"},
            "floor": {"lake_files": lake_files, "manifest_exists": manifest_path().exists()}}


# ── 7. backup ─────────────────────────────────────────────────────────────────

def backup(dest: str, verify_samples: int = 8) -> dict:
    """Sync lake/ + manifest/ to dest (pure-add), then verify by re-hashing a sample.
    Writes only OUTSIDE the floor (M10). The verify can fail — that is the point."""
    import json as _json
    import random
    d = Path(dest).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    home = brain_home()
    copied = 0
    for src in [home / "lake", home / "manifest"]:
        if not src.exists():
            continue
        for f in src.rglob("*"):
            if f.is_file():
                rel = f.relative_to(home)
                target = d / rel
                if not target.exists() or target.stat().st_size != f.stat().st_size:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, target)
                    copied += 1
    # verify: sample manifest entries, re-hash the copied span source files
    lake_copies = list((d / "lake").rglob("*.jsonl")) if (d / "lake").exists() else []
    sample = random.sample(lake_copies, min(verify_samples, len(lake_copies)))
    verified = 0
    mismatches = []
    for f in sample:
        rel = f.relative_to(d)
        orig = home / rel
        if orig.exists() and hashlib.sha256(f.read_bytes()).hexdigest() == hashlib.sha256(orig.read_bytes()).hexdigest():
            verified += 1
        else:
            mismatches.append(str(rel))
    env = _envelope(f"copy lake/+manifest/ -> {d}; sha256 re-hash of {len(sample)} sampled files at dest")
    return {**env, "dest": str(d), "files_copied": copied,
            "verified": f"{verified}/{len(sample)}", "mismatches": mismatches or None}
