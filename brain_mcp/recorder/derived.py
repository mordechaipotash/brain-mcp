"""Derived layer — dialect views over the schema-free floor, one messages table, FTS.

The floor stays schema-free; each VIEW alone knows one agent's vocabulary
(bob's v_jsonl_cc pattern — and raw payload stays reachable through the floor).
derived.messages is rebuilt idempotently (anti-join append); the FTS index is
NOT incremental (docs-measured) → rebuilt only when messages grew, debounced
by the caller (P3: ~10s at 74k rows — fine at >=5min cadence, hot at 60s).
"""

from __future__ import annotations

from .floor_db import write_conn, read_conn

DIALECT_SQL = """
CREATE SCHEMA IF NOT EXISTS dialects;
CREATE SCHEMA IF NOT EXISTS derived;

-- latest-generation floor rows only
CREATE OR REPLACE VIEW dialects.v_live AS
SELECT r.* FROM floor.raw_lines r
JOIN floor.ingest_state s ON s.file_id = r.file_id AND s.witness_gen = r.witness_gen;

CREATE OR REPLACE VIEW dialects.v_cc AS
SELECT
  r.file_id, r.witness_gen, r.line_no, f.abs_path, f.session_hint AS session_id,
  r.event_time, r.captured_at,
  json_extract_string(r.raw_line, '$.type')       AS ev_type,
  json_extract_string(r.raw_line, '$.message.role')  AS role,
  json_extract_string(r.raw_line, '$.message.model') AS model,
  json_extract(r.raw_line, '$.message.content')   AS content,
  TRY_CAST(json_extract_string(r.raw_line, '$.isSidechain') AS BOOLEAN) AS is_sidechain,
  json_extract_string(r.raw_line, '$.cwd')        AS cwd
FROM dialects.v_live r JOIN floor.files f USING (file_id)
WHERE r.agent = 'cc' AND json_valid(r.raw_line);

CREATE OR REPLACE VIEW dialects.v_codex AS
SELECT
  r.file_id, r.witness_gen, r.line_no, f.abs_path, f.session_hint AS session_id,
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
  q.file_id, q.witness_gen, q.line_no, q.abs_path, q.session_hint AS session_id,
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

CREATE OR REPLACE VIEW derived.v_messages_all AS
SELECT 'cc:'||session_id||':'||witness_gen||':'||line_no AS msg_id,
       'claude-code' AS agent, session_id, role, model,
       derived.text_of(content) AS text, event_time, captured_at,
       file_id, witness_gen, line_no
FROM dialects.v_cc
WHERE ev_type IN ('user','assistant') AND NOT coalesce(is_sidechain, false)
  AND role IN ('user','assistant')
UNION ALL BY NAME
SELECT 'codex:'||session_id||':'||witness_gen||':'||line_no AS msg_id,
       'codex' AS agent, session_id AS session_id, role AS role, NULL AS model,
       derived.text_of(content) AS text, event_time AS event_time,
       captured_at AS captured_at, file_id AS file_id,
       witness_gen AS witness_gen, line_no AS line_no
FROM dialects.v_codex
WHERE envelope_type='response_item' AND body_type='message' AND role IN ('user','assistant')
UNION ALL BY NAME
SELECT 'pi:'||session_id||':'||witness_gen||':'||line_no AS msg_id,
       'pi' AS agent, session_id AS session_id, role AS role, model AS model,
       derived.text_of(content) AS text, event_time AS event_time,
       captured_at AS captured_at, file_id AS file_id,
       witness_gen AS witness_gen, line_no AS line_no
FROM dialects.v_pi
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
    table breaks every INSERT on upgrade. Rows are copied, never re-derived:
    migrate-v1 rows have no floor bytes behind them and could not come back.

    Returns True when the table was reshaped (the caller must rebuild FTS).
    """
    want = [r[0] for r in c.execute("DESCRIBE derived.v_messages_all").fetchall()]
    have = [r[0] for r in c.execute("DESCRIBE derived.messages").fetchall()]
    if have == want:
        return False
    keep = ", ".join(f'"{col}"' for col in want if col in have)
    c.execute("BEGIN TRANSACTION")
    try:
        c.execute("DROP TABLE IF EXISTS derived.messages_reshape")
        c.execute("CREATE TABLE derived.messages_reshape AS "
                  "SELECT * FROM derived.v_messages_all WITH NO DATA")
        c.execute(f"INSERT INTO derived.messages_reshape ({keep}) "
                  f"SELECT {keep} FROM derived.messages WHERE msg_id IS NOT NULL")
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


def search_fts(query: str, limit: int = 10, min_rank: float = 0.0,
               agent: str | None = None, role: str | None = None) -> list[dict]:
    """BM25 hits, best-first. Empty list = caller renders the abstention (M1)."""
    where, params = [], [query]
    if agent:
        where.append("agent = ?")
        params.append(agent)
    if role:
        where.append("role = ?")
        params.append(role)
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
                     file_id, witness_gen, line_no,
                     fts_derived_messages.match_bm25(msg_id, ?) AS rank
              FROM derived.messages{extra}
            )
            WHERE rank IS NOT NULL AND rank >= {float(min_rank)}
            ORDER BY rank DESC LIMIT {int(limit)}
            """,
            params,
        ).fetchall()
        cols = ["msg_id","agent","session_id","role","model","text","event_time",
                "captured_at","file_id","witness_gen","line_no","rank"]
    return [dict(zip(cols, r)) for r in rows]
