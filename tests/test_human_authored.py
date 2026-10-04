# brain 2.1 — role="user" means what the PERSON wrote.
"""Through 2.0.0, role="user" returned whatever the harness put under the user role.
Measured 2026-10-04 on the author's machine: 24.9% of Claude Code user-text rows (80
newest sessions) and 67% of Codex user messages (40 newest) were injected text. The
Claude Code rule is is_him() from the author's m-plugin hooks/lib/turns.py, plus
compaction summaries; slash-command wrappers are machine text, as in the author's
own corpus (2,007 of 2,007 marked not human)."""

import shutil
from pathlib import Path

FIX = Path(__file__).parent / "fixtures"
CC_SID = "bbbbbbbb-1111-2222-3333-444444444444"
CODEX_SID = "22222222-3333-4444-5555-666666666666"


def load(floor):
    proj = floor.projects / "-Users-test"
    proj.mkdir()
    shutil.copy(FIX / "sample_cc_mixed.jsonl", proj / f"{CC_SID}.jsonl")
    shutil.copy(FIX / "sample_codex_mixed.jsonl",
                floor.codex / f"rollout-2026-09-01T10-00-00-{CODEX_SID}.jsonl")
    floor.record()


def texts(resp):
    return sorted(h["excerpt"] for h in resp.get("hits", []))


class TestClaudeCode:
    def test_role_user_returns_only_what_the_person_typed(self, floor):
        from brain_mcp.recorder import api

        load(floor)
        assert texts(api.search("giraffe", role="user", agent="claude-code")) == [
            "and what about giraffe calves in october",
            "please explain the giraffe migration routes",
        ]

    def test_injected_text_is_one_flag_away(self, floor):
        from brain_mcp.recorder import api

        load(floor)
        machine = texts(api.search("giraffe", role="user", agent="claude-code",
                                   include_machine=True, limit=50))
        for marker in ("skill body", "/giraffe", "task-notification", "giraffe summary",
                       "Caveat:", "[Request interrupted", "system-reminder"):
            assert any(marker in t for t in machine), marker


class TestCodex:
    def test_injected_context_blocks_are_not_the_person(self, floor):
        from brain_mcp.recorder import api

        load(floor)
        got = texts(api.search("okapi", role="user", agent="codex"))
        assert got == ["find every okapi sighting from last week",
                       "how many okapi live in the reserve"], got

    def test_realtime_delegation_keeps_the_spoken_input_only(self, floor):
        from brain_mcp.recorder import api

        load(floor)
        hit = api.search("sighting", role="user", agent="codex")["hits"][0]
        assert hit["excerpt"] == "find every okapi sighting from last week"
        assert "transcript_delta" not in hit["excerpt"]


class TestDateWindow:
    def test_since_and_until_bound_the_search(self, floor):
        from brain_mcp.recorder import api

        load(floor)
        assert texts(api.search("giraffe", role="user", since="2026-10-01")) == [
            "and what about giraffe calves in october"]
        assert texts(api.search("giraffe", role="user", until="2026-09-01")) == [
            "please explain the giraffe migration routes"]
        empty = api.search("giraffe", role="user", since="2027-01-01")
        assert empty["abstained"] and empty["scope"]["since"] == "2027-01-01"


class TestUpgradeFrom200:
    def test_an_old_table_gets_the_new_columns_computed_not_defaulted(self, floor):
        """A 2.0.0 install's derived.messages has 11 columns. On the first 2.1 refresh the
        rows the view can re-create are re-derived, so a slash-command wrapper that 2.0.0
        stored as role=user comes back human_authored=false — computed. Rows with no floor
        bytes (v1) are kept, with the honest default."""
        from brain_mcp.recorder import api, derived
        from brain_mcp.recorder.floor_db import read_conn, write_conn

        load(floor)
        old = ("msg_id, agent, session_id, role, model, text, event_time, captured_at, "
               "file_id, witness_gen, line_no")
        with write_conn() as c:
            c.execute(f"CREATE TABLE derived.m200 AS SELECT {old} FROM derived.messages")
            c.execute("DROP TABLE derived.messages")
            c.execute("ALTER TABLE derived.m200 RENAME TO messages")
            c.execute("INSERT INTO derived.messages (msg_id, agent, session_id, role, text, witness_gen, line_no) "
                      "VALUES ('v1:chatgpt:z1', 'chatgpt', 'c1', 'user', 'a v1 giraffe question', 0, 0)")
        assert derived.refresh()["reshaped"] is True
        with read_conn() as c:
            wrapper = c.execute("SELECT human_authored FROM derived.messages "
                                "WHERE text LIKE '%<command-name>/giraffe%'").fetchone()
            v1 = c.execute("SELECT human_authored, is_subagent FROM derived.messages "
                           "WHERE msg_id = 'v1:chatgpt:z1'").fetchone()
        assert wrapper == (False,), "re-derived, not defaulted to role='user'"
        assert v1 == (True, False), "no floor bytes behind it: kept, with the honest default"
        assert texts(api.search("giraffe", role="user", agent="claude-code")) == [
            "and what about giraffe calves in october",
            "please explain the giraffe migration routes",
        ]
