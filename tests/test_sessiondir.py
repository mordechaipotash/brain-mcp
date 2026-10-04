# brain 2.1 — a Claude Code session is a folder, not a file.
"""Subagent and workflow transcripts, their .meta.json and tool-results/ live in
<project>/<session>/ beside the transcript. Through 2.0.0 none of it was recorded:
the session pattern matched 0 of 1,225 subagent files on the author's machine
(72% of Claude Code's files, about 24% of its bytes, measured 2026-10-04)."""

import shutil
from pathlib import Path

FIX = Path(__file__).parent / "fixtures"
SID = "aaaaaaaa-1111-2222-3333-444444444444"
SUB_LINES = (
    b'{"type":"user","isSidechain":true,"message":{"role":"user","content":"subagent task: '
    b'find the zebra"},"timestamp":"2026-10-01T10:00:00Z"}\n'
    b'{"type":"assistant","isSidechain":true,"message":{"role":"assistant","content":'
    b'[{"type":"text","text":"subagent finding: the zebra is in the north field"}]},'
    b'"timestamp":"2026-10-01T10:00:05Z"}\n'
)


def make_session(floor):
    proj = floor.projects / "-Users-test"
    folder = proj / SID
    (folder / "subagents").mkdir(parents=True)
    (folder / "tool-results").mkdir()
    shutil.copy(FIX / "sample_cc_mixed.jsonl", proj / f"{SID}.jsonl")
    (folder / "subagents" / "agent-abc123.jsonl").write_bytes(SUB_LINES)
    (folder / "subagents" / "agent-abc123.meta.json").write_bytes(b'{"agentType":"Explore"}')
    (folder / "tool-results" / "toolu_9.txt").write_bytes(b"a large tool output, no trailing newline")
    (proj / "memory").mkdir()
    (proj / "memory" / "MEMORY.md").write_bytes(b"not a session folder")
    return proj, folder


def lake(floor, rel):
    return floor.home / "lake" / "cc_sessiondir" / "-Users-test" / rel


class TestSessionFolderCapture:
    def test_every_file_in_the_folder_is_recorded_byte_exact(self, floor):
        _, folder = make_session(floor)
        floor.record()
        for rel in ("subagents/agent-abc123.jsonl", "subagents/agent-abc123.meta.json",
                    "tool-results/toolu_9.txt"):
            held = lake(floor, f"{SID}/{rel}")
            assert held.exists(), rel
            assert held.read_bytes() == (folder / rel).read_bytes(), rel

    def test_a_folder_that_is_not_a_session_is_skipped(self, floor):
        make_session(floor)
        floor.record()
        assert not (floor.home / "lake" / "cc_sessiondir" / "-Users-test" / "memory").exists()

    def test_a_changed_file_opens_a_new_generation_and_keeps_the_old(self, floor):
        _, folder = make_session(floor)
        floor.record()
        (folder / "tool-results" / "toolu_9.txt").write_bytes(b"rewritten, and longer than before")
        floor.record()
        g1 = lake(floor, f"{SID}/tool-results/toolu_9.txt")
        g2 = lake(floor, f"{SID}/tool-results/toolu_9.g2.txt")
        assert g1.read_bytes() == b"a large tool output, no trailing newline"
        assert g2.read_bytes() == b"rewritten, and longer than before"


class TestSubagentsInSearch:
    def test_subagent_text_is_out_of_default_search_and_in_on_request(self, floor):
        from brain_mcp.recorder import api

        make_session(floor)
        floor.record()
        assert api.search("zebra")["abstained"], "subagent chatter is not the person's conversation"
        hit = api.search("zebra", include_subagents=True)["hits"][0]["citation"]
        assert hit["file"] == f"lake/cc_sessiondir/-Users-test/{SID}/subagents/agent-abc123.jsonl"
        assert hit["session_id"] == SID, "a subagent row belongs to its parent session"
        got = api.get(hit["file"], hit["lines"], expect_sha256=hit["sha256"])
        assert got["verified"] is True
