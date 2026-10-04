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
import json
import os
import shlex
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import derived
from .floor_db import read_conn
from . import manifest
from .paths import brain_home, glob_root, lake_blob, lake_dir, lake_file, manifest_path, spool_dir


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
    rel = row.get("lake_file") or _lake_rel(row["agent"], row["session_id"], row["witness_gen"])
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
           limit: int = 10, min_rank: float = 0.0, order: str = "rank",
           since: str | None = None, until: str | None = None,
           include_machine: bool = False, include_subagents: bool = False) -> dict:
    hits = derived.search_fts(query, limit=limit, min_rank=min_rank, agent=agent, role=role,
                              since=since, until=until, include_machine=include_machine,
                              include_subagents=include_subagents)
    scope = {"since": since, "until": until, "include_machine": include_machine,
             "include_subagents": include_subagents}
    if order == "time_asc":
        hits.sort(key=lambda h: str(h["event_time"]))
    elif order == "time_desc":
        hits.sort(key=lambda h: str(h["event_time"]), reverse=True)
    cov = _coverage()
    env = _envelope(f"duckdb fts_bm25 over derived.messages ({cov['rows']} rows)", _unwatched())
    if not hits:
        return {**env, "abstained": True,
                "reason": f"no hit scored >= min_rank {min_rank} under fts_bm25 for query {query!r}",
                "coverage": cov, "scope": scope,
                "hint": "absence here is evidence only about the lanes, dates and scope searched; "
                        "try other keywords, widen since/until, or set include_machine / "
                        "include_subagents to search injected text and subagent transcripts",
                "hits": []}
    out = []
    for h in hits:
        text = h["text"]
        excerpt = text if len(text) <= 600 else text[:600] + f"… (excerpt: 600 of {len(text)} chars — full via brain_get)"
        out.append({"excerpt": excerpt, "rank": round(h["rank"], 3), "citation": _citation(h)})
    return {**env, "abstained": False, "ranker": "fts_bm25", "hits": out, "coverage": cov,
            "scope": scope}


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
           limit: int = 20, include_machine: bool = False, include_subagents: bool = False) -> dict:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    where, params = derived.scope_filters(agent, role, since, None, include_machine, include_subagents)
    with read_conn() as c:
        rows = c.execute(
            f"SELECT msg_id, agent, session_id, role, model, text, CAST(event_time AS VARCHAR), CAST(captured_at AS VARCHAR), "
            f"file_id, witness_gen, line_no, lake_file FROM derived.messages "
            f"WHERE {' AND '.join(where)} ORDER BY event_time DESC LIMIT {int(limit)}",
            params).fetchall()
        cols = ["msg_id","agent","session_id","role","model","text","event_time","captured_at",
                "file_id","witness_gen","line_no","lake_file"]
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
    where, params = ["NOT coalesce(is_subagent, false)"], []
    if date:
        where.append("CAST(event_time AS DATE) = ?"); params.append(date)
    if agent:
        where.append("agent = ?"); params.append(agent)
    w = ("WHERE " + " AND ".join(where)) if where else ""
    with read_conn() as c:
        rows = c.execute(
            f"SELECT agent, session_id, witness_gen, CAST(min(event_time) AS VARCHAR), CAST(max(event_time) AS VARCHAR), "
            f"count(*), sum(CASE WHEN role='user' AND coalesce(human_authored, true) THEN 1 ELSE 0 END) "
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
    return {**env, "lanes": lanes_out, "summary": counts, "receipt": receipt()}


# ── 5b. the receipt: what the agents deleted, and whether the floor still holds it ──

_MAIN_LANES = ("cc_transcript", "codex_rollout", "pi_session")


def _manifest_index() -> dict:
    """One pass over the manifest: the byte ranges each (lane, key, gen) was captured in,
    whole-file shas for blobs, and the origin_gone events already recorded."""
    chunks: dict[tuple, list] = {}
    blobs: dict[tuple, str] = {}
    gone: dict[str, dict] = {}
    mp = manifest_path()
    if mp.exists():
        with open(mp, "rb") as f:
            for raw in f:
                try:
                    e = json.loads(raw)
                except Exception:
                    continue
                if e.get("event") == "origin_gone":
                    gone.setdefault(e.get("file_id"), e)
                elif e.get("blob"):
                    blobs[(e.get("lane"), e.get("session"), e.get("gen"))] = e.get("sha256")
                elif "chunk_sha256" in e:
                    chunks.setdefault((e.get("lane"), e.get("session"), e.get("gen")), []).append(
                        (e["byte_from"], e["byte_to"], e["chunk_sha256"]))
    return {"chunks": chunks, "blobs": blobs, "gone": gone}


def _floor_path(idx: dict, lane: str, key: str, gen: int) -> tuple[Path, bool]:
    """(lake path, is_blob) for one captured file generation."""
    if (lane, key, gen) in idx["blobs"]:
        return lake_blob(lane, key, gen), True
    return lake_file(lane, key, gen), False


def _verify_held(idx: dict, lane: str, key: str, gen: int) -> tuple[bool, str]:
    """Do the floor's bytes still hash to what the manifest recorded at capture?"""
    p, is_blob = _floor_path(idx, lane, key, gen)
    if not p.exists():
        return False, f"floor file missing: {p.relative_to(brain_home())}"
    data = p.read_bytes()
    if is_blob:
        ok = hashlib.sha256(data).hexdigest() == idx["blobs"][(lane, key, gen)]
        return ok, "whole-file sha256 " + ("matches" if ok else "DIFFERS")
    spans = idx["chunks"].get((lane, key, gen), [])
    if not spans:
        return False, "no manifest record of this file's capture"
    bad = sum(1 for a, b, sha in spans if hashlib.sha256(data[a:b]).hexdigest() != sha)
    return bad == 0, f"{len(spans) - bad}/{len(spans)} captured chunks match their sha256"


def _floor_files() -> list[dict]:
    with read_conn() as c:
        rows = c.execute(
            "SELECT f.file_id, f.lane, f.abs_path, f.session_hint, s.witness_gen "
            "FROM floor.files f JOIN floor.ingest_state s USING (file_id)").fetchall()
    return [dict(zip(["file_id", "lane", "abs_path", "key", "gen"], r)) for r in rows]


def _session_of(lane: str, key: str) -> str:
    return key.split("/")[1] if lane == "cc_sessiondir" and "/" in key else key


def notice_gone() -> int:
    """WRITER (run by `record`): for each file newly seen gone from its origin, re-hash its
    floor bytes against every chunk the manifest recorded at capture and append an
    origin_gone event. The receipt's dates are therefore when the recorder NOTICED —
    measured, never inferred — and the readers below stay read-only (M10)."""
    idx = _manifest_index()
    n = 0
    for f in _floor_files():
        ap = f["abs_path"] or ""
        if ap.startswith("spool://") or Path(ap).exists() or f["file_id"] in idx["gone"]:
            continue
        ok, detail = _verify_held(idx, f["lane"], f["key"], f["gen"])
        manifest.append({"event": "origin_gone", "file_id": f["file_id"], "lane": f["lane"],
                         "session": f["key"], "gen": f["gen"], "origin_path": ap,
                         "noticed_at": manifest.now_iso(), "verified": ok, "detail": detail})
        n += 1
    return n


def receipt() -> dict:
    """READER: files whose origin is gone (deleted or moved) while the floor still holds
    them, with the verification `record` performed when it first noticed each one. A file
    gone since the last `record` shows as pending, never as verified."""
    idx = _manifest_index()
    out_files = []
    for f in _floor_files():
        ap = f["abs_path"] or ""
        if ap.startswith("spool://") or Path(ap).exists():
            continue
        ev = idx["gone"].get(f["file_id"]) or {}
        out_files.append({**f, "noticed_at": ev.get("noticed_at"), "verified": ev.get("verified"),
                          "detail": ev.get("detail") or "noticed now; verified on the next record"})
    sessions = sorted({(f["lane"], _session_of(f["lane"], f["key"])) for f in out_files
                       if f["lane"] in _MAIN_LANES})
    pending = [f for f in out_files if f["verified"] is None]
    unverified = [f for f in out_files if f["verified"] is False]
    noticed = sorted(f["noticed_at"] for f in out_files if f["noticed_at"])
    if not out_files:
        line = "No captured file is missing from where its agent kept it."
    else:
        line = (f"{len(sessions)} session(s) and {len(out_files)} file(s) are gone from where the "
                f"agent kept them (deleted or moved); the floor holds every one"
                + ("." if not unverified else
                   f", but {len(unverified)} could not be verified against the manifest — see files.")
                + (f" {len(pending)} noticed since the last record, verified on the next." if pending else "")
                + " brain-mcp restore --list shows what can be put back.")
    return {"line": line, "sessions_gone": len(sessions), "files_gone": len(out_files),
            "all_verified": not unverified and not pending, "pending": len(pending),
            "first_noticed": noticed[0] if noticed else None,
            "last_noticed": noticed[-1] if noticed else None,
            "sessions": [{"lane": l, "session": sid} for l, sid in sessions[:50]],
            "files": [{"lane": f["lane"], "origin_path": f["abs_path"], "verified": f["verified"],
                       "detail": f["detail"], "noticed_at": f["noticed_at"]}
                      for f in (unverified + pending)[:20]]}


# ── 5c. restore: put a session back where its agent will find it (CLI only) ──

def restore(session: str, to: str | None = None, dry_run: bool = False,
            accept_redacted: bool = False) -> dict:
    """Write a session's floor copy back to its origin path (or under `to`), byte-exact.

    All-or-nothing: if any target already exists with DIFFERENT bytes, nothing is written
    (identical bytes are a no-op). Session-folder files (subagents, tool-results, .meta.json)
    come back with the transcript. Not an MCP tool: it writes outside the floor, into the
    agent's own directories, so it is a deliberate CLI act."""
    idx = _manifest_index()
    lanes = {l["lane"]: l for l in _lanes()}
    files = [f for f in _floor_files()
             if (f["lane"] in _MAIN_LANES and f["key"] == session)
             or (f["lane"] == "cc_sessiondir" and _session_of(f["lane"], f["key"]) == session)]
    if not files:
        return {"ok": False, "error": f"no floor files for session {session}"}
    plan, conflicts, problems = [], [], []
    for f in sorted(files, key=lambda x: (x["lane"] != "cc_sessiondir", x["key"])):
        src, is_blob = _floor_path(idx, f["lane"], f["key"], f["gen"])
        if not src.exists():
            problems.append(f"floor copy missing: {src}")
            continue
        data = src.read_bytes()
        if not is_blob and not accept_redacted and any(
                ln.startswith(b'{"redacted":true') for ln in data.splitlines()):
            problems.append(f"{src.name} carries redaction tombstones — pass --accept-redacted "
                            f"to restore it anyway (the agent will see the tombstone lines)")
            continue
        origin = f["abs_path"] or ""
        if to:
            root = glob_root(lanes.get(f["lane"], {}).get("root_glob", "/"))
            try:
                rel = Path(origin).relative_to(root)
            except ValueError:
                rel = Path(Path(origin).name)
            target = Path(to).expanduser() / rel
        elif origin.startswith("spool://"):
            problems.append(f"{f['key']}: origin path never observed (hook-only capture) — use --to")
            continue
        else:
            target = Path(origin)
        sha = hashlib.sha256(data).hexdigest()
        state = "write"
        if target.exists():
            state = "identical" if hashlib.sha256(target.read_bytes()).hexdigest() == sha else "conflict"
        item = {"lane": f["lane"], "from": str(src.relative_to(brain_home())), "to": str(target),
                "bytes": len(data), "sha256": sha, "action": state}
        (conflicts if state == "conflict" else plan).append(item)
    if problems or conflicts:
        return {"ok": False, "written": 0, "conflicts": conflicts, "problems": problems,
                "note": "nothing was written — restore is all-or-nothing and never overwrites "
                        "different bytes"}
    written = 0
    if not dry_run:
        for item in plan:
            if item["action"] != "write":
                continue
            t = Path(item["to"])
            t.parent.mkdir(parents=True, exist_ok=True)
            tmp = t.with_name("." + t.name + ".brain-restore")
            tmp.write_bytes((brain_home() / item["from"]).read_bytes())
            os.replace(tmp, t)
            if hashlib.sha256(t.read_bytes()).hexdigest() != item["sha256"]:
                return {"ok": False, "written": written, "error": f"post-write sha mismatch at {t}"}
            manifest.append({"event": "restored", "lane": item["lane"], "session": session,
                             "from": item["from"], "to": item["to"], "sha256": item["sha256"],
                             "restored_at": manifest.now_iso()})
            written += 1
    main = next((f for f in files if f["lane"] in _MAIN_LANES), None)
    hint = None
    if main and main["lane"] == "cc_transcript":
        with read_conn() as c:
            cwd = c.execute(
                "SELECT json_extract_string(raw_line, '$.cwd') FROM floor.raw_lines "
                "WHERE file_id = ? AND json_valid(raw_line) AND json_extract_string(raw_line, '$.cwd') IS NOT NULL "
                "ORDER BY line_no LIMIT 1", [main["file_id"]]).fetchone()
        hint = (f"cd {shlex.quote(cwd[0])} && claude --resume {session}" if cwd and cwd[0]
                else f"claude --resume {session}  (from the session's original working directory)")
    elif main and main["lane"] == "codex_rollout":
        hint = f"codex resume {session}"
    return {"ok": True, "dry_run": dry_run, "written": written, "files": plan,
            "resume": hint if not to else None}


def restore_list() -> dict:
    """Sessions whose origin is gone and that the floor can put back."""
    r = receipt()
    return {"sessions": r["sessions"], "sessions_gone": r["sessions_gone"],
            "all_verified": r["all_verified"], "line": r["line"]}


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
