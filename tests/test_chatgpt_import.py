# brain-mcp — ChatGPT export → chatgpt_export lane origin
"""Tests for the ChatGPT data-export converter.

The export is a tree, the floor is content-hashed, and the old v1 ingester
dropped data on both counts. These tests pin the three properties that matter:
every message node survives, the output is byte-stable across runs, and the
emitted shape is the one the dialect view reads.
"""

import hashlib
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

    def test_keeps_system_and_tool_nodes(self):
        """The floor keeps what the export held; the dialect view decides what search shows."""
        from brain_mcp.recorder.import_chatgpt import conversation_lines

        conv = _conv()
        conv["mapping"]["n8"] = {
            "id": "n8", "parent": "n2", "children": [],
            "message": {"author": {"role": "system"}, "create_time": 1673557440.0,
                        "content": {"content_type": "text", "parts": ["hidden"]}},
        }
        conv["mapping"]["n9"] = {   # code-interpreter output keeps its text outside `parts`
            "id": "n9", "parent": "n2", "children": [],
            "message": {"author": {"role": "tool", "name": "python"}, "create_time": 1673557441.0,
                        "content": {"content_type": "execution_output", "text": "42"}},
        }

        _, lines = conversation_lines(conv)

        records = [json.loads(l)["message"] for l in lines]
        assert [r["role"] for r in records] == ["user", "assistant", "system", "tool"]
        assert records[-1]["content"] == [{"type": "text", "text": "42"}]

    def test_a_node_without_a_role_is_not_a_turn(self):
        from brain_mcp.recorder.import_chatgpt import conversation_lines

        conv = _conv()
        conv["mapping"]["n9"] = {
            "id": "n9", "parent": "n2", "children": [],
            "message": {"author": {}, "create_time": 1673557440.0,
                        "content": {"content_type": "text", "parts": ["orphan"]}},
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

    def test_truncated_download_is_reported_as_a_broken_archive(self, tmp_path):
        """No end-of-archive record: still a ZIP problem, not a JSON problem."""
        from brain_mcp.recorder.import_chatgpt import convert

        good = _zip(tmp_path, {"conversations.json": [_conv()]}).read_bytes()
        bad = tmp_path / "cut.zip"
        bad.write_bytes(good[: len(good) // 2])

        census = convert([bad], dest=tmp_path / "out")

        assert census["conversations"] == 0
        assert len(census["errors"]) == 1
        assert "BadZipFile" in census["errors"][0]

    def test_corrupt_compressed_member_is_reported_not_raised(self, tmp_path):
        """A damaged deflate stream raises zlib.error, which must not escape."""
        from brain_mcp.recorder.import_chatgpt import convert

        archive = tmp_path / "export.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("conversations.json", json.dumps([_conv()] * 50))
        raw = bytearray(archive.read_bytes())
        start = raw.index(b"conversations.json") + len("conversations.json")
        for i in range(start + 4, start + 24):   # inside the compressed payload
            raw[i] ^= 0xFF
        archive.write_bytes(bytes(raw))

        census = convert([archive], dest=tmp_path / "out")

        assert census["conversations"] == 0
        assert census["errors"] and "conversations.json" in census["errors"][0]

    def test_duplicate_member_names_are_both_read(self, tmp_path):
        from brain_mcp.recorder.import_chatgpt import convert

        archive = tmp_path / "dup.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("conversations.json", json.dumps([_conv()]))
            zf.writestr("conversations.json",
                        json.dumps([_conv(id="99999999-8888-7777-6666-555555555555")]))

        dest = tmp_path / "out"
        census = convert([archive], dest=dest)

        assert census["conversations"] == 2
        assert {p.stem for p in dest.glob("*.jsonl")} == {
            "11111111-2222-3333-4444-555555555555",
            "99999999-8888-7777-6666-555555555555",
        }

    def test_backslash_member_path_is_found(self, tmp_path):
        from brain_mcp.recorder.import_chatgpt import convert

        archive = _zip(tmp_path, {"export\\conversations.json": [_conv()]})

        census = convert([archive], dest=tmp_path / "out")

        assert census["conversations"] == 1

    def test_utf8_bom_is_tolerated(self, tmp_path):
        from brain_mcp.recorder.import_chatgpt import convert

        archive = _zip(tmp_path, {"conversations.json": b"\xef\xbb\xbf" + json.dumps([_conv()]).encode()})

        census = convert([archive], dest=tmp_path / "out")

        assert census["conversations"] == 1 and census["errors"] == []

    def test_directory_without_exports_points_at_the_zip(self, tmp_path):
        from brain_mcp.recorder.import_chatgpt import convert

        _zip(tmp_path, {"conversations.json": [_conv()]})

        census = convert([tmp_path], dest=tmp_path / "out")

        assert census["conversations"] == 0
        assert any("pass the .zip itself" in e for e in census["errors"])

    def test_members_are_read_never_extracted(self, tmp_path, monkeypatch):
        """A hostile member name is just a name: nothing is ever extracted."""
        import zipfile as zf_mod
        from brain_mcp.recorder.import_chatgpt import convert

        def boom(*a, **k):
            raise AssertionError("extract must not be used")

        monkeypatch.setattr(zf_mod.ZipFile, "extract", boom)
        monkeypatch.setattr(zf_mod.ZipFile, "extractall", boom)
        archive = _zip(tmp_path, {"../conversations.json": [_conv()]})
        dest = tmp_path / "out"
        before = {p for p in tmp_path.rglob("*")}

        census = convert([archive], dest=dest)

        assert census["conversations"] == 1
        created = {p for p in tmp_path.rglob("*")} - before
        assert all(dest == p or dest in p.parents for p in created)
        assert not (tmp_path.parent / "conversations.json").exists()


class TestProvenance:
    """Where an import came from: hashes of what was read, on the manifest."""

    def test_zip_and_member_hashes_match_sha256sum(self, tmp_path):
        from brain_mcp.recorder.import_chatgpt import convert

        archive = _zip(tmp_path, {"conversations.json": [_conv()], "user.json": {"id": "x"}})
        member_bytes = json.dumps([_conv()]).encode()

        prov = convert([archive], dest=tmp_path / "out")["provenance"]

        (export,) = prov["exports"]
        assert export["name"] == "export.zip"
        assert export["size"] == archive.stat().st_size
        assert export["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
        (member,) = export["members"]
        assert member["path"] == "conversations.json"
        assert member["size"] == len(member_bytes)
        assert member["sha256"] == hashlib.sha256(member_bytes).hexdigest()
        assert member["conversations"] == 1
        assert prov["conversations"] == 1

    def test_plain_file_is_its_own_member(self, tmp_path):
        from brain_mcp.recorder.import_chatgpt import convert

        src = _write_export(tmp_path, [_conv()])

        (export,) = convert([src], dest=tmp_path / "out")["provenance"]["exports"]

        digest = hashlib.sha256(src.read_bytes()).hexdigest()
        assert export["sha256"] == digest
        assert export["members"][0]["sha256"] == digest

    def test_directory_records_one_export_per_file(self, tmp_path):
        from brain_mcp.recorder.import_chatgpt import convert

        _write_export(tmp_path, [_conv()], name="conversations-000.json")
        _write_export(tmp_path, [_conv(id="99999999-8888-7777-6666-555555555555")],
                      name="conversations-001.json")

        exports = convert([tmp_path], dest=tmp_path / "out")["provenance"]["exports"]

        assert [e["name"] for e in exports] == ["conversations-000.json", "conversations-001.json"]

    def test_unreadable_member_is_on_the_record_with_its_error(self, tmp_path):
        from brain_mcp.recorder.import_chatgpt import convert

        archive = _zip(tmp_path, {"conversations-000.json": [_conv()],
                                  "conversations-001.json": "{not json"})

        (export,) = convert([archive], dest=tmp_path / "out")["provenance"]["exports"]

        by_path = {m["path"]: m for m in export["members"]}
        assert "conversations" in by_path["conversations-000.json"]
        assert "JSONDecodeError" in by_path["conversations-001.json"]["error"]
        assert by_path["conversations-001.json"]["sha256"]  # what was read is still pinned

    def test_import_id_does_not_depend_on_source_order(self, tmp_path):
        from brain_mcp.recorder.import_chatgpt import convert

        a = _write_export(tmp_path, [_conv()], name="conversations-000.json")
        b = _write_export(tmp_path, [_conv(id="99999999-8888-7777-6666-555555555555")],
                          name="conversations-001.json")

        one = convert([a, b], dest=tmp_path / "o1")["provenance"]["import_id"]
        two = convert([b, a], dest=tmp_path / "o2")["provenance"]["import_id"]

        assert one == two

    def test_a_repacked_zip_gets_its_own_id(self, tmp_path):
        from brain_mcp.recorder.import_chatgpt import convert

        first = _zip(tmp_path, {"conversations.json": [_conv()]}, name="a.zip")
        second = _zip(tmp_path, {"conversations.json": [_conv()], "extra.txt": "x"}, name="b.zip")

        assert (convert([first], dest=tmp_path / "o1")["provenance"]["import_id"]
                != convert([second], dest=tmp_path / "o2")["provenance"]["import_id"])


def _events(home: Path) -> list[dict]:
    mp = home / "manifest" / "manifest.jsonl"
    return [json.loads(l) for l in mp.read_text().splitlines() if '"event":"import"' in l] \
        if mp.exists() else []


class TestRecordImport:
    """The manifest event: an event log with double-start protection."""

    def _prov(self, tmp_path, *convs, name="conversations.json"):
        from brain_mcp.recorder.import_chatgpt import convert

        src = _write_export(tmp_path, list(convs), name=name)
        return convert([src], dest=tmp_path / f"out-{name}")["provenance"]

    def test_event_carries_the_ask_and_stays_invisible_to_manifest_readers(self, floor, tmp_path):
        from brain_mcp.recorder import api
        from brain_mcp.recorder.import_chatgpt import record_import

        prov = self._prov(tmp_path, _conv())
        before = api._manifest_index()

        assert record_import(prov) is True

        (event,) = _events(floor.home)
        (export,) = event["exports"]
        assert {"name", "size", "sha256"} <= set(export)
        assert event["conversations"] == 1
        assert event["converter"]["name"] == "import_chatgpt" and event["converter"]["v"] >= 2
        assert not ({"blob", "chunk_sha256"} & set(event)) and event["event"] != "origin_gone"
        assert api._manifest_index() == before   # chunks / blobs / gone untouched

    def test_immediate_repeat_is_suppressed(self, floor, tmp_path):
        from brain_mcp.recorder.import_chatgpt import record_import

        prov = self._prov(tmp_path, _conv())

        assert record_import(prov) is True
        assert record_import(prov) is False
        assert len(_events(floor.home)) == 1

    def test_a_real_reimport_after_another_import_is_recorded_again(self, floor, tmp_path):
        from brain_mcp.recorder.import_chatgpt import record_import

        a = self._prov(tmp_path, _conv(), name="a.json")
        b = self._prov(tmp_path, _conv(id="99999999-8888-7777-6666-555555555555"), name="b.json")

        assert [record_import(p) for p in (a, b, a)] == [True, True, True]
        assert len(_events(floor.home)) == 3

    def test_nothing_is_recorded_when_no_conversation_was_read(self, floor, tmp_path):
        from brain_mcp.recorder.import_chatgpt import convert, record_import

        bad = tmp_path / "conversations.json"
        bad.write_text("{not json", encoding="utf-8")
        prov = convert([bad], dest=tmp_path / "out")["provenance"]

        assert record_import(prov) is False
        assert _events(floor.home) == []


class TestImportEndToEnd:
    """`import-chatgpt` through the real CLI path: convert, record, scan, derive."""

    def _run(self, source, capsys):
        import argparse

        from brain_mcp.recorder import cli

        rc = cli.cmd_import_chatgpt(argparse.Namespace(source=[str(source)]))
        out = capsys.readouterr().out
        return rc, json.loads(out[: out.rindex("}") + 1])

    def _rows(self):
        from brain_mcp.recorder.floor_db import read_conn

        with read_conn() as c:
            return c.execute(
                "SELECT msg_id, role, human_authored, is_subagent, lake_file, witness_gen "
                "FROM derived.messages WHERE agent = 'chatgpt' ORDER BY line_no").fetchall()

    def test_rows_carry_the_2_1_columns_and_cite_an_existing_file(self, floor, tmp_path, capsys):
        from brain_mcp.recorder import api

        archive = _zip(tmp_path, {"conversations.json": [_conv()]})

        rc, out = self._run(archive, capsys)

        assert rc == 0 and out["provenance_recorded"] is True
        rows = self._rows()
        assert len(rows) == 2
        for msg_id, role, human, sub, lake_file, gen in rows:
            assert human == (role == "user") and sub is False
            assert lake_file == f"lake/chatgpt_export/11111111-2222-3333-4444-555555555555.jsonl"
            assert gen == 1
        hits = api.recent(hours=24 * 365 * 10, agent="chatgpt")["hits"]
        assert len(hits) == 2   # the default filters keep them: user text is "human", not a subagent
        for hit in hits:
            assert hit["citation"]["file"] == rows[0][4]
            assert hit["citation"]["sha256"] is not None   # the file exists and the span hashes

    def test_rerun_records_nothing_new_and_inserts_no_duplicates(self, floor, tmp_path, capsys):
        archive = _zip(tmp_path, {"conversations.json": [_conv()]})
        self._run(archive, capsys)

        rc, out = self._run(archive, capsys)

        assert rc == 0
        assert out["import"]["written"] == 0 and out["provenance_recorded"] is False
        assert len(_events(floor.home)) == 1
        assert len(self._rows()) == 2

    def test_a_busy_database_does_not_fail_an_import_that_succeeded(self, floor, tmp_path,
                                                                     capsys, monkeypatch):
        """The lane is a watch lane: origin files and the import record are already in place,
        so a held database lock only postpones capture to the next `record` tick."""
        import argparse

        import duckdb

        from brain_mcp.recorder import cli, scanner

        def busy(*a, **k):
            raise duckdb.IOException('IO Error: Could not set lock on file "brain.duckdb"')

        archive = _zip(tmp_path, {"conversations.json": [_conv()]})
        # A scoped patch, never monkeypatch.undo(): that would also undo the fixture's
        # BRAIN_HOME and send the next scan at the developer's real ~/.brain.
        with monkeypatch.context() as m:
            m.setattr(scanner, "scan_tick", busy)
            rc = cli.cmd_import_chatgpt(argparse.Namespace(source=[str(archive)]))
        captured = capsys.readouterr()

        assert rc == 0
        assert "next `record` tick will capture them" in captured.err
        out = json.loads(captured.out[: captured.out.rindex("}") + 1])
        assert out["captured"] is False and out["provenance_recorded"] is True
        assert (floor.home / "imports" / "chatgpt" / "11111111-2222-3333-4444-555555555555.jsonl").exists()
        assert len(_events(floor.home)) == 1
        assert str(floor.home).startswith(str(tmp_path))   # still the isolated home
        floor.record()                                  # the next tick takes them in
        assert len(self._rows()) == 2

    def test_a_real_held_lock_is_survived_too(self, floor, tmp_path, capsys):
        """The same, against an actual second process holding the database."""
        import argparse
        import subprocess
        import sys
        import time

        from brain_mcp.recorder import cli
        from brain_mcp.recorder.paths import db_path

        holder = subprocess.Popen([sys.executable, "-c",
                                   f"import duckdb,time; c=duckdb.connect({str(db_path())!r}); "
                                   f"print('held', flush=True); time.sleep(30)"],
                                  stdout=subprocess.PIPE, text=True)
        try:
            assert holder.stdout.readline().strip() == "held"
            archive = _zip(tmp_path, {"conversations.json": [_conv()]})

            rc = cli.cmd_import_chatgpt(argparse.Namespace(source=[str(archive)]))
            err = capsys.readouterr().err
        finally:
            holder.kill()
            holder.wait()

        assert rc == 0 and "holds the database lock" in err
        assert len(_events(floor.home)) == 1

    def test_system_and_tool_nodes_reach_the_origin_but_not_search(self, floor, tmp_path, capsys):
        conv = _conv()
        conv["mapping"]["n8"] = {"id": "n8", "parent": "n2", "children": [], "message": {
            "author": {"role": "system"}, "create_time": 1673557440.0,
            "content": {"content_type": "text", "parts": ["hidden"]}}}
        conv["mapping"]["n9"] = {"id": "n9", "parent": "n2", "children": [], "message": {
            "author": {"role": "tool"}, "create_time": 1673557441.0,
            "content": {"content_type": "execution_output", "text": "42"}}}

        rc, _ = self._run(_zip(tmp_path, {"conversations.json": [conv]}), capsys)

        origin = floor.home / "imports" / "chatgpt" / "11111111-2222-3333-4444-555555555555.jsonl"
        assert rc == 0 and len(origin.read_text().splitlines()) == 4   # the floor keeps all four
        assert [r[1] for r in self._rows()] == ["user", "assistant"]   # search view is unchanged

    def test_a_second_copy_of_a_conversation_is_a_second_file(self, floor, tmp_path, capsys):
        """2.1.1: the same uuid at two paths is two files, keyed <uuid>~<folder>."""
        from brain_mcp.recorder import derived
        from brain_mcp.recorder.paths import imports_dir
        from brain_mcp.recorder.scanner import scan_tick

        self._run(_zip(tmp_path, {"conversations.json": [_conv()]}), capsys)
        origin = imports_dir("chatgpt")
        name = "11111111-2222-3333-4444-555555555555.jsonl"
        (origin / "copy").mkdir()
        (origin / "copy" / name).write_bytes((origin / name).read_bytes())

        scan_tick(only_lane="chatgpt_export")
        derived.refresh()

        rows = self._rows()
        assert len({r[0] for r in rows}) == 4                       # msg_ids do not collide
        assert {r[4] for r in rows} == {f"lake/chatgpt_export/{name}",
                                        f"lake/chatgpt_export/{name[:-6]}~copy.jsonl"}
        assert all((floor.home / r[4]).exists() for r in rows)      # both citations resolve


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
