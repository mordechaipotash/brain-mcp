# brain v2 — derived layer: every recorded agent is searchable, and upgrades keep every row.
"""Issue #8 and PR #9 (sidey79), plus the upgrade trap PR #9 alone would have opened.

Through 2.0.0b3 the Codex and Pi branches of derived.v_messages_all had unnamed
columns, so their text never reached derived.messages and brain_search could
not see a single Codex or Pi message. The same bug gave every b3 table 16
columns instead of 11, so fixing the view alone breaks INSERT on upgrade."""

import argparse
import os
import shutil
from pathlib import Path

import duckdb
import pytest

FIX = Path(__file__).parent / "fixtures"
CC_SESSION = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
CODEX_SESSION = "11111111-2222-3333-4444-555555555555"
PI_SESSION = "99999999-8888-7777-6666-555555555555"

# The exact b3 shape, measured on a fresh 2.0.0b3 install on 2026-10-04.
B3_JUNK_COLUMNS = [
    "((((('codex:' || session_id) || ':') || witness_gen) || ':') || line_no)",
    "'codex'",
    'derived.text_of("content")',
    "((((('pi:' || session_id) || ':') || witness_gen) || ':') || line_no)",
    "'pi'",
]


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_HOME", str(tmp_path / "brainhome"))
    roots = {
        "cc_transcript": (tmp_path / "claude", f"{CC_SESSION}.jsonl", "sample_claude_code.jsonl"),
        "codex_rollout": (tmp_path / "codex", f"rollout-2026-08-20T09-00-00-{CODEX_SESSION}.jsonl",
                          "sample_codex.jsonl"),
        "pi_session": (tmp_path / "pi", f"{PI_SESSION}.jsonl", "sample_pi.jsonl"),
    }
    from brain_mcp.recorder import floor_db, paths

    paths.ensure_layout(list(roots) + ["cc_sessiondir"])
    floor_db.init_db()
    with floor_db.write_conn() as c:
        for lane, (root, name, fixture) in roots.items():
            root.mkdir(parents=True)
            shutil.copy(FIX / fixture, root / name)
            c.execute("UPDATE floor.lanes SET root_glob = ? WHERE lane = ?",
                      [str(root / "**/*.jsonl"), lane])
        # every seeded lane must point inside tmp, or scan_tick() reads the real home
        c.execute("UPDATE floor.lanes SET root_glob = ? WHERE lane = 'cc_sessiondir'",
                  [str(tmp_path / "claude" / "*" / "*" / "**" / "*")])
        unpinned = c.execute("SELECT lane FROM floor.lanes WHERE root_glob LIKE '~%'").fetchall()
    assert not unpinned, f"lanes still pointing at the real home: {unpinned}"
    return Path(os.environ["BRAIN_HOME"])


def _record():
    from brain_mcp.recorder import derived
    from brain_mcp.recorder.scanner import scan_tick

    scan_tick()
    return derived.refresh()


def _columns(table):
    from brain_mcp.recorder.floor_db import read_conn

    with read_conn() as c:
        return [r[0] for r in c.execute(f"DESCRIBE {table}").fetchall()]


class TestEveryAgentSearchable:
    def test_codex_and_pi_reach_the_index(self, home):
        from brain_mcp.recorder.derived import search_fts

        _record()
        assert search_fts("kafka", agent="codex"), "Codex text must be searchable (PR #9)"
        assert search_fts("parser", agent="pi"), "Pi text must be searchable (PR #9)"
        assert search_fts("asyncio", agent="claude-code")

    def test_no_message_lands_without_id_or_text(self, home):
        from brain_mcp.recorder.floor_db import read_conn

        _record()
        with read_conn() as c:
            agents = {r[0] for r in c.execute("SELECT DISTINCT agent FROM derived.messages").fetchall()}
            bad = c.execute("SELECT count(*) FROM derived.messages "
                            "WHERE msg_id IS NULL OR agent IS NULL OR text IS NULL").fetchone()[0]
        assert agents == {"claude-code", "codex", "pi"}
        assert bad == 0

    def test_view_has_exactly_the_named_columns(self, home):
        """PR #9's bug gave the view expression-named junk columns ("'codex'", ...).
        Pin the exact list: 11 through 2.0.0, plus three in 2.1."""
        _record()
        cols = _columns("derived.v_messages_all")
        assert cols == ["msg_id", "agent", "session_id", "role", "model", "text", "event_time",
                        "captured_at", "file_id", "witness_gen", "line_no", "human_authored",
                        "is_subagent", "lake_file"], cols
        assert cols == _columns("derived.messages")


class TestUpgradeFromB3:
    def _plant_b3_table(self, home):
        """A b3 install's derived.messages: 11 real columns + 5 junk, one CC row, one v1 row."""
        from brain_mcp.recorder.floor_db import write_conn

        junk = ", ".join(f'"{name.replace(chr(34), chr(34) * 2)}" VARCHAR' for name in B3_JUNK_COLUMNS)
        with write_conn() as c:
            c.execute("CREATE SCHEMA IF NOT EXISTS derived")
            c.execute(
                "CREATE TABLE derived.messages (msg_id VARCHAR, agent VARCHAR, session_id VARCHAR, "
                "role VARCHAR, model VARCHAR, text VARCHAR, event_time TIMESTAMPTZ, "
                f"captured_at TIMESTAMPTZ, file_id VARCHAR, witness_gen SMALLINT, line_no INTEGER, {junk})"
            )
            c.execute("INSERT INTO derived.messages (msg_id, agent, session_id, role, text, witness_gen, line_no) "
                      "VALUES ('cc:legacy:1:1', 'claude-code', 'legacy', 'user', 'a row recorded before the upgrade', 1, 1), "
                      "('v1:chatgpt:abc', 'chatgpt', 'conv', 'user', 'a migrated v1 row with no floor bytes', 0, 0)")
        assert len(_columns("derived.messages")) == 16

    def test_refresh_reshapes_and_keeps_every_row(self, home):
        from brain_mcp.recorder.derived import search_fts
        from brain_mcp.recorder.floor_db import read_conn

        self._plant_b3_table(home)
        result = _record()
        assert result["reshaped"] is True
        assert result["fts_rebuilt"] is True
        assert _columns("derived.messages") == _columns("derived.v_messages_all")
        with read_conn() as c:
            kept = {r[0] for r in c.execute("SELECT msg_id FROM derived.messages "
                                            "WHERE msg_id IN ('cc:legacy:1:1', 'v1:chatgpt:abc')").fetchall()}
        assert kept == {"cc:legacy:1:1", "v1:chatgpt:abc"}, "upgrade must never lose a row"
        assert search_fts("migrated")
        assert search_fts("kafka", agent="codex")

    def test_second_refresh_is_a_no_op(self, home):
        self._plant_b3_table(home)
        _record()
        again = _record()
        assert again["reshaped"] is False
        assert again["new_messages"] == 0


class TestMigrateV1:
    def test_v1_rows_land_on_a_fresh_install(self, home, tmp_path):
        from brain_mcp.recorder import cli
        from brain_mcp.recorder.floor_db import read_conn

        parquet = tmp_path / "v1.parquet"
        duckdb.sql(
            "SELECT 'chatgpt' AS source, 'm1' AS message_id, 'conv1' AS conversation_id, "
            "'user' AS role, NULL::VARCHAR AS model, 'an old v1 message about sourdough' AS content, "
            "0 AS timestamp_is_fallback, TIMESTAMPTZ '2025-01-01 00:00:00+00' AS msg_timestamp"
        ).write_parquet(str(parquet))
        assert cli.cmd_migrate_v1(argparse.Namespace(parquet=str(parquet))) == 0
        with read_conn() as c:
            n = c.execute("SELECT count(*) FROM derived.messages WHERE msg_id = 'v1:chatgpt:m1'").fetchone()[0]
        assert n == 1


class TestLambdaSyntax:
    def test_dialect_sql_needs_no_single_arrow(self, home):
        """Issue #8: DuckDB 1.6 rejects `x -> ...`. Forbid it here on every DuckDB we run on."""
        from brain_mcp.recorder import derived
        from brain_mcp.recorder.floor_db import write_conn

        with write_conn() as c:
            try:
                c.execute("SET lambda_syntax = 'DISABLE_SINGLE_ARROW'")
            except duckdb.Error:
                pass  # setting gone in later DuckDB, where single arrows are already off
            c.execute(derived.DIALECT_SQL)
            text = c.execute("SELECT derived.text_of('[{\"type\":\"output_text\",\"text\":\"hi\"}]'::JSON)").fetchone()[0]
        assert text == "hi"
