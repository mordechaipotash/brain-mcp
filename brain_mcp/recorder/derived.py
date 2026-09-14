"""Derived layer — dialect views over the schema-free floor, one messages table, FTS.

The floor stays schema-free; each VIEW alone knows one agent's vocabulary
(bob's v_jsonl_cc pattern — and raw payload stays reachable through the floor).
derived.messages is rebuilt idempotently (anti-join append); the FTS index is
NOT incremental (docs-measured) → rebuilt only when messages grew, debounced
by the caller (P3: ~10s at 74k rows — fine at >=5min cadence, hot at 60s).
"""

from __future__ import annotations

from .floor_db import write_conn, read_conn

# raw string: the SQL carries regex escapes (\s, \.) meant for DuckDB, not Python
DIALECT_SQL = r"""
CREATE SCHEMA IF NOT EXISTS dialects;
CREATE SCHEMA IF NOT EXISTS derived;

-- latest-generation floor rows only
CREATE OR REPLACE VIEW dialects.v_live AS
SELECT r.* FROM floor.raw_lines r
JOIN floor.ingest_state s ON s.file_id = r.file_id AND s.witness_gen = r.witness_gen;

CREATE OR REPLACE VIEW dialects.v_cc AS
SELECT
  r.file_id, r.witness_gen, r.line_no, r.lane, f.abs_path, f.session_hint AS file_key,
  -- a session-folder file is keyed <project>/<session>/<rest>; its session is the folder.
  -- A second copy of a session (same uuid, another project folder) is keyed <uuid>~<project>:
  -- it is the same session, so session_id is the uuid; file_key keeps the copies apart.
  CASE WHEN r.lane = 'cc_sessiondir' THEN split_part(f.session_hint, '/', 2)
       ELSE split_part(f.session_hint, '~', 1) END AS session_id,
  r.event_time, r.captured_at,
  json_extract_string(r.raw_line, '$.type')       AS ev_type,
  json_extract_string(r.raw_line, '$.message.role')  AS role,
  json_extract_string(r.raw_line, '$.message.model') AS model,
  json_extract(r.raw_line, '$.message.content')   AS content,
  TRY_CAST(json_extract_string(r.raw_line, '$.isSidechain') AS BOOLEAN) AS is_sidechain,
  TRY_CAST(json_extract_string(r.raw_line, '$.isMeta') AS BOOLEAN) AS is_meta,
  TRY_CAST(json_extract_string(r.raw_line, '$.isCompactSummary') AS BOOLEAN) AS is_compact,
  json_extract_string(r.raw_line, '$.cwd')        AS cwd
FROM dialects.v_live r JOIN floor.files f USING (file_id)
WHERE r.agent = 'cc' AND json_valid(r.raw_line);

CREATE OR REPLACE VIEW dialects.v_codex AS
SELECT
  r.file_id, r.witness_gen, r.line_no, r.lane, f.abs_path, f.session_hint AS file_key,
  split_part(f.session_hint, '~', 1) AS session_id,
  coalesce(r.event_time, TRY_CAST(json_extract_string(r.raw_line,'$.timestamp') AS TIMESTAMPTZ)) AS event_time,
  r.captured_at,
  json_extract_string(r.raw_line, '$.type')            AS envelope_type,
  json_extract_string(r.raw_line, '$.payload.type')    AS body_type,
  json_extract_string(r.raw_line, '$.payload.role')    AS role,
  json_extract(r.raw_line, '$.payload.content')        AS content
FROM dialects.v_live r JOIN floor.files f USING (file_id)
WHERE r.agent = 'codex' AND json_valid(r.raw_line);

CREATE OR REPLACE VIEW dialects.v_pi AS
SELECT
  q.file_id, q.witness_gen, q.line_no, q.lane, q.abs_path, q.session_hint AS file_key,
  split_part(q.session_hint, '~', 1) AS session_id,
  q.event_time, q.captured_at, q.ev_type,
  last_value(CASE WHEN q.ev_type='model_change'
             THEN json_extract_string(q.raw_line,'$.modelId') END IGNORE NULLS)
    OVER (PARTITION BY q.file_id, q.witness_gen ORDER BY q.line_no
          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS model,
  json_extract_string(q.raw_line, '$.message.role')    AS role,
  json_extract(q.raw_line, '$.message.content')        AS content
FROM (SELECT r.*, f.abs_path, f.session_hint,
             json_extract_string(r.raw_line,'$.type') AS ev_type
      FROM dialects.v_live r JOIN floor.files f USING (file_id)
      WHERE r.agent = 'pi' AND json_valid(r.raw_line)) q;

CREATE OR REPLACE VIEW dialects.v_chatgpt AS
SELECT
  r.file_id, r.witness_gen, r.line_no, f.abs_path, f.session_hint AS session_id,
  r.event_time, r.captured_at,
  json_extract_string(r.raw_line, '$.type')          AS ev_type,
  json_extract_string(r.raw_line, '$.message.role')  AS role,
  json_extract_string(r.raw_line, '$.message.model') AS model,
  json_extract(r.raw_line, '$.message.content')      AS content,
  json_extract_string(r.raw_line, '$.title')         AS title
FROM dialects.v_live r JOIN floor.files f USING (file_id)
WHERE r.agent = 'chatgpt' AND json_valid(r.raw_line);

-- shared text extraction over the common block-array shape
CREATE OR REPLACE MACRO derived.text_of(content) AS
  CASE WHEN content IS NULL THEN NULL
       WHEN json_type(content) = 'VARCHAR' THEN json_extract_string(content, '$')
       WHEN json_type(content) = 'ARRAY' THEN
         array_to_string(list_transform(
           list_filter(json_extract(content,'$[*]'),
             lambda x: json_extract_string(x,'$.type') IN ('text','input_text','output_text')),
           lambda x: json_extract_string(x,'$.text')), chr(10))
       ELSE NULL END;

CREATE OR REPLACE MACRO derived.lstrip_ws(t) AS ltrim(t, ' ' || chr(9) || chr(10) || chr(13));

-- Claude Code text that the harness injected under role=user. The rule is is_him() from
-- the author's m-plugin hooks/lib/turns.py, whose every exclusion was earned by replaying
-- real transcripts, plus compaction summaries (both marked not-human in the author's own
-- corpus: 2,007 slash-command wrappers and 1,296 summaries, measured 2026-10-04).
CREATE OR REPLACE MACRO derived.cc_injected(t) AS
  t IS NULL
  OR starts_with(derived.lstrip_ws(t), '<command-')
  OR starts_with(derived.lstrip_ws(t), '<task-notification>')
  OR starts_with(derived.lstrip_ws(t), '[Request interrupted')
  OR starts_with(derived.lstrip_ws(t), '[Base]')
  OR starts_with(derived.lstrip_ws(t), 'Caveat:')
  OR starts_with(derived.lstrip_ws(t), '<channel ')
  OR starts_with(derived.lstrip_ws(t), 'This session is being continued from a previous conversation')
  OR contains(t, '<local-command-')
  OR contains(left(derived.lstrip_ws(t), 200), 'system-reminder');

-- Codex wraps injected context as a user message that opens with a tag block
-- (<environment_context>, <recommended_plugins>, <codex_delegation>, <skill>, ...) or the
-- AGENTS.md preamble: 67% of the author's Codex user messages, measured 2026-10-04.
CREATE OR REPLACE MACRO derived.codex_injected(t) AS
  t IS NULL OR regexp_matches(derived.lstrip_ws(t), '^(<[A-Za-z_][A-Za-z0-9_-]*(\s[^>]*)?>|# AGENTS\.md)');

-- Codex realtime voice delegates a spoken request as <realtime_delegation><input>...</input>
-- <transcript_delta>...: the <input> is the person's own words, the delta is a log.
CREATE OR REPLACE MACRO derived.codex_realtime_input(t) AS
  CASE WHEN starts_with(derived.lstrip_ws(t), '<realtime_delegation')
       THEN nullif(trim(regexp_extract(t, '(?s)<input>(.*?)</input>', 1)), '') END;

-- the floor file a row cites, for every lane (session-folder keys carry their path)
CREATE OR REPLACE MACRO derived.lake_rel(lane, file_key, gen) AS
  'lake/' || lane || '/' || file_key || CASE WHEN gen = 1 THEN '' ELSE '.g' || gen END || '.jsonl';

CREATE OR REPLACE VIEW derived.v_messages_all AS
SELECT CASE WHEN lane = 'cc_sessiondir' THEN 'ccsub:' || file_id ELSE 'cc:' || file_key END
         || ':' || witness_gen || ':' || line_no AS msg_id,
       'claude-code' AS agent, session_id AS session_id, role AS role, model AS model,
       t0 AS text, event_time AS event_time, captured_at AS captured_at,
       file_id AS file_id, witness_gen AS witness_gen, line_no AS line_no,
       (role = 'user' AND NOT coalesce(is_sidechain, false) AND NOT coalesce(is_meta, false)
        AND NOT coalesce(is_compact, false) AND NOT derived.cc_injected(t0)) AS human_authored,
       (coalesce(is_sidechain, false) OR lane = 'cc_sessiondir') AS is_subagent,
       derived.lake_rel(lane, file_key, witness_gen) AS lake_file
FROM (SELECT *, derived.text_of(content) AS t0 FROM dialects.v_cc
      WHERE ev_type IN ('user','assistant') AND role IN ('user','assistant')) x
UNION ALL BY NAME
SELECT 'codex:' || file_key || ':' || witness_gen || ':' || line_no AS msg_id,
       'codex' AS agent, session_id AS session_id, role AS role, NULL::VARCHAR AS model,
       coalesce(rt, t0) AS text, event_time AS event_time, captured_at AS captured_at,
       file_id AS file_id, witness_gen AS witness_gen, line_no AS line_no,
       (role = 'user' AND (rt IS NOT NULL OR NOT derived.codex_injected(t0))) AS human_authored,
       false AS is_subagent,
       derived.lake_rel(lane, file_key, witness_gen) AS lake_file
FROM (SELECT *, derived.text_of(content) AS t0,
             derived.codex_realtime_input(derived.text_of(content)) AS rt
      FROM dialects.v_codex
      WHERE envelope_type='response_item' AND body_type='message' AND role IN ('user','assistant')) x
UNION ALL BY NAME
SELECT 'pi:' || file_key || ':' || witness_gen || ':' || line_no AS msg_id,
       'pi' AS agent, session_id AS session_id, role AS role, model AS model,
       derived.text_of(content) AS text, event_time AS event_time,
       captured_at AS captured_at, file_id AS file_id,
       witness_gen AS witness_gen, line_no AS line_no,
       (role = 'user') AS human_authored,
       false AS is_subagent,
       derived.lake_rel(lane, file_key, witness_gen) AS lake_file
FROM dialects.v_pi
WHERE ev_type='message' AND role IN ('user','assistant')
UNION ALL BY NAME
SELECT 'chatgpt:'||session_id||':'||witness_gen||':'||line_no AS msg_id,
       'chatgpt' AS agent, session_id AS session_id, role AS role, model AS model,
       derived.text_of(content) AS text, event_time AS event_time,
       captured_at AS captured_at, file_id AS file_id,
       witness_gen AS witness_gen, line_no AS line_no
FROM dialects.v_chatgpt
WHERE ev_type='message' AND role IN ('user','assistant');

CREATE TABLE IF NOT EXISTS derived.messages AS
  SELECT * FROM derived.v_messages_all WITH NO DATA;
"""


def ensure_shape(c) -> bool:
    """Make derived.messages carry exactly the view's columns, keeping every row.

    The table is created once, from whatever the view looked like at the time.
    Through 2.0.0b3 the Codex and Pi branches had unnamed columns, so UNION ALL BY
    NAME gave the view (and every table built from it) 16 columns instead of 11 —
    measured on a b3 install, 2026-10-04. Fixing the view without fixing the
    table breaks every INSERT on upgrade.

    A row the view will produce again is left to the view, so new columns are COMPUTED
    rather than left NULL (2.1 added human_authored, is_subagent, lake_file). Every other
    row is copied: migrate-v1 rows have no floor bytes behind them, and rows from an older
    generation of a rewritten file are no longer in the view — neither could come back.
    Copied rows get the honest default for a new column: human_authored = (role = 'user'),
    is_subagent = false.

    Returns True when the table was reshaped (the caller must rebuild FTS).
    """
    want = [r[0] for r in c.execute("DESCRIBE derived.v_messages_all").fetchall()]
    have = [r[0] for r in c.execute("DESCRIBE derived.messages").fetchall()]
    if have == want:
        return False
    keep = ", ".join(f'"{col}"' for col in want if col in have)
    keep_m = ", ".join(f'm."{col}"' for col in want if col in have)
    c.execute("BEGIN TRANSACTION")
    try:
        c.execute("DROP TABLE IF EXISTS derived.messages_reshape")
        c.execute("CREATE TABLE derived.messages_reshape AS "
                  "SELECT * FROM derived.v_messages_all WITH NO DATA")
        c.execute(f"INSERT INTO derived.messages_reshape ({keep}) "
                  f"SELECT {keep_m} FROM derived.messages m "
                  f"ANTI JOIN (SELECT msg_id FROM derived.v_messages_all WHERE msg_id IS NOT NULL) v "
                  f"USING (msg_id) WHERE m.msg_id IS NOT NULL")
        if "human_authored" not in have:
            c.execute("UPDATE derived.messages_reshape SET human_authored = (role = 'user') "
                      "WHERE human_authored IS NULL")
        if "is_subagent" not in have:
            c.execute("UPDATE derived.messages_reshape SET is_subagent = false WHERE is_subagent IS NULL")
        c.execute("DROP TABLE derived.messages")
        c.execute("ALTER TABLE derived.messages_reshape RENAME TO messages")
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise
    return True


def refresh(rebuild_fts: bool | None = None) -> dict:
    """Idempotent: (re)create views, append new messages, rebuild FTS if grew.

    Returns {"new_messages": n, "total": m, "fts_rebuilt": bool, "reshaped": bool}.
    """
    with write_conn() as c:
        c.execute(DIALECT_SQL)
        reshaped = ensure_shape(c)
        before = c.execute("SELECT count(*) FROM derived.messages").fetchone()[0]
        c.execute(
            "INSERT INTO derived.messages BY NAME "
            "SELECT v.* FROM derived.v_messages_all v "
            "ANTI JOIN derived.messages m USING (msg_id) "
            "WHERE v.text IS NOT NULL AND length(v.text) > 0"
        )
        total = c.execute("SELECT count(*) FROM derived.messages").fetchone()[0]
        grew = total > before
        do_fts = (grew or reshaped) if rebuild_fts is None else rebuild_fts
        if do_fts:
            c.execute("INSTALL fts; LOAD fts;")
            c.execute("PRAGMA create_fts_index('derived.messages', 'msg_id', 'text', overwrite=1)")
    return {"new_messages": total - before, "total": total, "fts_rebuilt": bool(do_fts),
            "reshaped": reshaped}


def scope_filters(agent: str | None = None, role: str | None = None,
                  since: str | None = None, until: str | None = None,
                  include_machine: bool = False, include_subagents: bool = False) -> tuple[list[str], list]:
    """The WHERE clauses every reader of derived.messages shares.

    By default a reader sees what a person and an assistant said to each other: text the
    harness injected under role=user (skill bodies, reminders, slash wrappers, compaction
    summaries, Codex context blocks) is excluded, and so are subagent transcripts, which
    are agent-to-agent work, not the person's conversation. Both opt back in.
    `since`/`until` bound event_time; a bare date for `until` includes that whole day.
    """
    where, params = [], []
    if agent:
        where.append("agent = ?"); params.append(agent)
    if role:
        where.append("role = ?"); params.append(role)
    if not include_machine:
        where.append("(role <> 'user' OR coalesce(human_authored, true))")
    if not include_subagents:
        where.append("NOT coalesce(is_subagent, false)")
    if since:
        where.append("event_time >= CAST(? AS TIMESTAMPTZ)"); params.append(since)
    if until:
        if len(until) == 10:  # a bare date: the whole day counts
            where.append("event_time < CAST(? AS DATE) + INTERVAL 1 DAY")
        else:
            where.append("event_time <= CAST(? AS TIMESTAMPTZ)")
        params.append(until)
    return where, params


def search_fts(query: str, limit: int = 10, min_rank: float = 0.0,
               agent: str | None = None, role: str | None = None,
               since: str | None = None, until: str | None = None,
               include_machine: bool = False, include_subagents: bool = False) -> list[dict]:
    """BM25 hits, best-first. Empty list = caller renders the abstention (M1)."""
    where, fparams = scope_filters(agent, role, since, until, include_machine, include_subagents)
    params = [query] + fparams
    extra = (" WHERE " + " AND ".join(where)) if where else ""
    # The score is computed in a subquery and filtered outside it: DuckDB 1.6
    # refuses a WHERE that names a SELECT alias whose expression has side effects
    # ("Alias "rank" referenced"), which match_bm25 does. Measured 2026-10-04 on
    # 1.6.0.dev379 — the same release line as issue #8.
    with read_conn() as c:
        c.execute("LOAD fts;")
        rows = c.execute(
            f"""
            SELECT * FROM (
              SELECT msg_id, agent, session_id, role, model, text, CAST(event_time AS VARCHAR) AS event_time, CAST(captured_at AS VARCHAR) AS captured_at,
                     file_id, witness_gen, line_no, human_authored, is_subagent, lake_file,
                     fts_derived_messages.match_bm25(msg_id, ?) AS rank
              FROM derived.messages{extra}
            )
            WHERE rank IS NOT NULL AND rank >= {float(min_rank)}
            ORDER BY rank DESC LIMIT {int(limit)}
            """,
            params,
        ).fetchall()
        cols = ["msg_id","agent","session_id","role","model","text","event_time",
                "captured_at","file_id","witness_gen","line_no","human_authored","is_subagent",
                "lake_file","rank"]
    return [dict(zip(cols, r)) for r in rows]
