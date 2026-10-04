# brain-mcp — ChatGPT export → chatgpt_export lane origin
"""Tests for the ChatGPT data-export converter.

The export is a tree, the floor is content-hashed, and the old v1 ingester
dropped data on both counts. These tests pin the three properties that matter:
every message node survives, the output is byte-stable across runs, and the
emitted shape is the one the dialect view reads.
"""

import json
import uuid
import zipfile
from pathlib import Path


def _conv(**over):
    """A two-turn conversation in the export's own shape."""
    base = {
        "id": "11111111-2222-3333-4444-555555555555",
        "title": "A title",
        "create_time": 1673557436.0,
        "mapping": {
            "root": {"id": "root", "message": None, "parent": None, "children": ["n1"]},
            "n1": {
                "id": "n1",
                "parent": "root",
                "children": ["n2"],
                "message": {
                    "author": {"role": "user"},
                    "create_time": 1673557437.0,
                    "content": {"content_type": "text", "parts": ["ask"]},
                },
            },
            "n2": {
                "id": "n2",
                "parent": "n1",
                "children": [],
                "message": {
                    "author": {"role": "assistant"},
                    "create_time": 1673557438.0,
                    "content": {"content_type": "text", "parts": ["answer"]},
                    "metadata": {"model_slug": "gpt-4o"},
                },
            },
        },
    }
    base.update(over)
    return base


def _write_export(tmp_path: Path, conversations: list, name="conversations.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(conversations), encoding="utf-8")
    return p


class TestConversationLines:
    """Tree → ordered JSONL, without dropping branches."""

    def test_emits_one_line_per_message_node(self):
        from brain_mcp.recorder.import_chatgpt import conversation_lines

        conv_id, lines = conversation_lines(_conv())

        assert conv_id == "11111111-2222-3333-4444-555555555555"
        assert len(lines) == 2
        roles = [json.loads(l)["message"]["role"] for l in lines]
        assert roles == ["user", "assistant"]

    def test_keeps_every_branch_of_a_regenerated_answer(self):
        """A regenerated answer is a sibling node, not a replacement."""
        from brain_mcp.recorder.import_chatgpt import conversation_lines

        conv = _conv()
        conv["mapping"]["n1"]["children"].append("n3")
        conv["mapping"]["n3"] = {
            "id": "n3", "parent": "n1", "children": [],
            "message": {
                "author": {"role": "assistant"},
                "create_time": 1673557439.0,
                "content": {"content_type": "text", "parts": ["second answer"]},
            },
        }

        _, lines = conversation_lines(conv)

        texts = [json.loads(l)["message"]["content"][0]["text"] for l in lines]
        assert texts == ["ask", "answer", "second answer"]
        # The branch point stays reconstructable.
        assert json.loads(lines[2])["parent_id"] == "n1"

    def test_orders_by_the_messages_own_clock(self):
        from brain_mcp.recorder.import_chatgpt import conversation_lines

        conv = _conv()
        conv["mapping"]["n1"]["message"]["create_time"] = 9999999999.0

        _, lines = conversation_lines(conv)

        assert [json.loads(l)["message"]["role"] for l in lines] == ["assistant", "user"]

    def test_skips_system_and_tool_authors(self):
        from brain_mcp.recorder.import_chatgpt import conversation_lines

        conv = _conv()
        conv["mapping"]["n9"] = {
            "id": "n9", "parent": "n2", "children": [],
            "message": {"author": {"role": "system"}, "create_time": 1673557440.0,
                        "content": {"content_type": "text", "parts": ["hidden"]}},
        }

        _, lines = conversation_lines(conv)

        assert len(lines) == 2

    def test_keeps_short_messages(self):
        """v1 dropped anything under 5 characters; 'ok' is still a turn."""
        from brain_mcp.recorder.import_chatgpt import conversation_lines

        conv = _conv()
        conv["mapping"]["n1"]["message"]["content"]["parts"] = ["ok"]

        _, lines = conversation_lines(conv)

        assert json.loads(lines[0])["message"]["content"][0]["text"] == "ok"

    def test_drops_only_genuinely_empty_messages(self):
        from brain_mcp.recorder.import_chatgpt import conversation_lines

        conv = _conv()
        conv["mapping"]["n1"]["message"]["content"]["parts"] = [""]

        _, lines = conversation_lines(conv)

        assert len(lines) == 1

    def test_accepts_plain_string_content(self):
        from brain_mcp.recorder.import_chatgpt import conversation_lines

        conv = _conv()
        conv["mapping"]["n1"]["message"]["content"] = "bare string"

        _, lines = conversation_lines(conv)

        assert json.loads(lines[0])["message"]["content"] == [
            {"type": "text", "text": "bare string"}
        ]

    def test_accepts_dict_parts(self):
        from brain_mcp.recorder.import_chatgpt import conversation_lines

        conv = _conv()
        conv["mapping"]["n1"]["message"]["content"]["parts"] = [
            {"content_type": "text", "text": "from a dict part"}
        ]

        _, lines = conversation_lines(conv)

        assert json.loads(lines[0])["message"]["content"][0]["text"] == "from a dict part"

    def test_timestamp_is_utc_iso(self):
        from brain_mcp.recorder.import_chatgpt import conversation_lines

        _, lines = conversation_lines(_conv())

        assert json.loads(lines[0])["timestamp"] == "2023-01-12T21:03:57+00:00"

    def test_model_slug_is_carried(self):
        from brain_mcp.recorder.import_chatgpt import conversation_lines

        _, lines = conversation_lines(_conv())

        assert json.loads(lines[1])["message"]["model"] == "gpt-4o"

    def test_missing_id_yields_a_stable_uuid(self):
        from brain_mcp.recorder.import_chatgpt import conversation_lines

        def without_id():
            c = _conv()
            del c["id"]
            return c

        first, _ = conversation_lines(without_id())
        second, _ = conversation_lines(without_id())

        assert uuid.UUID(first)  # parses as a UUID, so the lane pattern matches
        assert first == second   # and does not move between runs


class TestConvert:
    """Writing the origin directory."""

    def test_writes_one_file_per_conversation(self, tmp_path):
        from brain_mcp.recorder.import_chatgpt import convert

        src = _write_export(tmp_path, [_conv()])
        dest = tmp_path / "out"

        census = convert([src], dest=dest)

        assert census["conversations"] == 1
        assert census["messages"] == 2
        assert census["written"] == 1
        assert (dest / "11111111-2222-3333-4444-555555555555.jsonl").exists()

    def test_second_run_rewrites_nothing(self, tmp_path):
        """The floor is content-hashed: a rewrite would open a new generation."""
        from brain_mcp.recorder.import_chatgpt import convert

        src = _write_export(tmp_path, [_conv()])
        dest = tmp_path / "out"

        convert([src], dest=dest)
        before = (dest / "11111111-2222-3333-4444-555555555555.jsonl").read_bytes()
        census = convert([src], dest=dest)
        after = (dest / "11111111-2222-3333-4444-555555555555.jsonl").read_bytes()

        assert census["written"] == 0
        assert census["unchanged"] == 1
        assert before == after

    def test_reads_every_chunk_of_a_split_export(self, tmp_path):
        """Large exports arrive as conversations-000.json, -001.json, …"""
        from brain_mcp.recorder.import_chatgpt import convert

        _write_export(tmp_path, [_conv()], name="conversations-000.json")
        _write_export(tmp_path, [_conv(id="99999999-8888-7777-6666-555555555555")],
                      name="conversations-001.json")
        dest = tmp_path / "out"

        census = convert([tmp_path], dest=dest)

        assert census["files_read"] == 2
        assert census["conversations"] == 2
        assert len(list(dest.glob("*.jsonl"))) == 2

    def test_malformed_export_is_reported_not_raised(self, tmp_path):
        from brain_mcp.recorder.import_chatgpt import convert

        bad = tmp_path / "conversations.json"
        bad.write_text("{not json", encoding="utf-8")

        census = convert([bad], dest=tmp_path / "out")

        assert census["files_read"] == 0
        assert len(census["errors"]) == 1
        assert "JSONDecodeError" in census["errors"][0]

    def test_leaves_no_partial_file_behind(self, tmp_path):
        from brain_mcp.recorder.import_chatgpt import convert

        src = _write_export(tmp_path, [_conv()])
        dest = tmp_path / "out"

        convert([src], dest=dest)

        assert not list(dest.glob(".*"))  # tmp files are dot-prefixed while partial


def _zip(tmp_path: Path, members: dict, name="export.zip") -> Path:
    p = tmp_path / name
    with zipfile.ZipFile(p, "w") as zf:
        for member, payload in members.items():
            zf.writestr(member, payload if isinstance(payload, (str, bytes)) else json.dumps(payload))
    return p


class TestConvertZip:
    """ChatGPT hands out a .zip; it is read in place, never extracted."""

    def test_zip_yields_the_same_bytes_as_the_unzipped_export(self, tmp_path):
        from brain_mcp.recorder.import_chatgpt import convert

        convs = [_conv()]
        plain = _write_export(tmp_path, convs)
        archive = _zip(tmp_path, {"conversations.json": convs})

        convert([plain], dest=tmp_path / "a")
        census = convert([archive], dest=tmp_path / "b")

        name = "11111111-2222-3333-4444-555555555555.jsonl"
        assert census["errors"] == []
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()

    def test_zip_reads_every_chunk_of_a_split_export(self, tmp_path):
        from brain_mcp.recorder.import_chatgpt import convert

        archive = _zip(tmp_path, {
            "conversations-000.json": [_conv()],
            "conversations-001.json": [_conv(id="99999999-8888-7777-6666-555555555555")],
            "user.json": {"id": "not a conversation list"},
        })

        census = convert([archive], dest=tmp_path / "out")

        assert census["files_read"] == 2
        assert census["conversations"] == 2
        assert census["errors"] == []

    def test_zip_member_in_a_subfolder_is_found(self, tmp_path):
        from brain_mcp.recorder.import_chatgpt import convert

        archive = _zip(tmp_path, {"export-2026/conversations.json": [_conv()]})

        census = convert([archive], dest=tmp_path / "out")

        assert census["conversations"] == 1

    def test_second_zip_run_rewrites_nothing(self, tmp_path):
        from brain_mcp.recorder.import_chatgpt import convert

        archive = _zip(tmp_path, {"conversations.json": [_conv()]})
        dest = tmp_path / "out"

        convert([archive], dest=dest)
        census = convert([archive], dest=dest)

        assert census["written"] == 0
        assert census["unchanged"] == 1

    def test_zip_without_conversations_is_reported_not_raised(self, tmp_path):
        from brain_mcp.recorder.import_chatgpt import convert

        archive = _zip(tmp_path, {"user.json": {}})

        census = convert([archive], dest=tmp_path / "out")

        assert census["conversations"] == 0
        assert any("no conversations*.json" in e for e in census["errors"])

    def test_corrupt_zip_is_reported_not_raised(self, tmp_path):
        from brain_mcp.recorder.import_chatgpt import convert

        bad = tmp_path / "export.zip"
        bad.write_bytes(b"PK\x03\x04 truncated")

        census = convert([bad], dest=tmp_path / "out")

        assert census["conversations"] == 0
        assert census["errors"]

    def test_hostile_member_name_writes_nothing_outside_dest(self, tmp_path):
        """Members are read, never extracted: a path-traversal name is just a name."""
        from brain_mcp.recorder.import_chatgpt import convert

        archive = _zip(tmp_path, {"../conversations.json": [_conv()]})
        dest = tmp_path / "out"

        convert([archive], dest=dest)

        assert not (tmp_path.parent / "conversations.json").exists()
        assert [p.name for p in dest.iterdir()] == ["11111111-2222-3333-4444-555555555555.jsonl"]


class TestLaneWiring:
    """The lane must be reachable by the scanner, or nothing is ever captured."""

    def test_scanner_extracts_a_session_id_from_the_filename(self):
        from brain_mcp.recorder.scanner import session_from_filename

        got = session_from_filename(
            "chatgpt_export", Path("11111111-2222-3333-4444-555555555555.jsonl")
        )

        assert got == "11111111-2222-3333-4444-555555555555"

    def test_lane_is_seeded_with_the_chatgpt_agent(self):
        from brain_mcp.recorder.floor_db import default_lanes

        lanes = {row[0]: row for row in default_lanes()}

        assert lanes["chatgpt_export"][1] == "chatgpt"
        assert lanes["chatgpt_export"][2] == "watch"

    def test_lane_glob_follows_brain_home(self, tmp_path, monkeypatch):
        """The origin lives inside BRAIN_HOME, so a relocated home must be followed."""
        from brain_mcp.recorder.floor_db import default_lanes

        monkeypatch.setenv("BRAIN_HOME", str(tmp_path))
        lanes = {row[0]: row for row in default_lanes()}

        assert lanes["chatgpt_export"][3].startswith(str(tmp_path))

    def test_citations_resolve_to_the_lane_directory(self):
        """A citation whose path is wrong reads back as sha256: null, not as an error."""
        from brain_mcp.recorder.api import _lake_rel

        rel = _lake_rel("chatgpt", "11111111-2222-3333-4444-555555555555", 1)

        assert rel == "lake/chatgpt_export/11111111-2222-3333-4444-555555555555.jsonl"

    def test_dialect_view_and_union_branch_exist(self):
        from brain_mcp.recorder.derived import DIALECT_SQL

        assert "dialects.v_chatgpt" in DIALECT_SQL
        assert "'chatgpt' AS agent" in DIALECT_SQL
