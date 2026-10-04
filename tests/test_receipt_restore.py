# brain 2.1 — the receipt, and putting a deleted session back.
"""Claude Code deletes sessions after cleanupPeriodDays (default 30). The floor already
held them byte-exact; 2.1 says so (the receipt) and puts one back (restore). Proven by
hand on 2026-10-04 before it was built: a deleted session's floor copy, copied back,
made `claude --resume` answer from the original conversation."""

import argparse
import shutil
from pathlib import Path

from tests.test_sessiondir import SID, make_session


def delete_session(proj, folder):
    (proj / f"{SID}.jsonl").unlink()
    shutil.rmtree(folder)


def manifest_lines(floor):
    return (floor.home / "manifest" / "manifest.jsonl").read_bytes().count(b"\n")


class TestReceipt:
    def test_the_receipt_counts_and_verifies_what_the_agent_deleted(self, floor):
        from brain_mcp.recorder import api

        proj, folder = make_session(floor)
        floor.record()
        assert api.receipt()["files_gone"] == 0
        delete_session(proj, folder)
        floor.record()
        r = api.receipt()
        assert (r["sessions_gone"], r["files_gone"], r["all_verified"]) == (1, 4, True), r
        assert "1 session(s) and 4 file(s)" in r["line"]
        assert api.health()["receipt"]["sessions_gone"] == 1

    def test_reading_the_receipt_writes_nothing(self, floor):
        """brain_health is a read-only MCP tool: noticing is record's job."""
        from brain_mcp.recorder import api

        proj, folder = make_session(floor)
        floor.record()
        delete_session(proj, folder)
        before = manifest_lines(floor)
        r = api.health()["receipt"]
        assert manifest_lines(floor) == before
        assert r["pending"] == 4 and r["all_verified"] is False

    def test_a_tampered_floor_copy_is_reported_not_verified(self, floor):
        from brain_mcp.recorder import api

        proj, folder = make_session(floor)
        floor.record()
        held = floor.home / "lake" / "cc_transcript" / f"{SID}.jsonl"
        held.write_bytes(held.read_bytes().replace(b"giraffe", b"GIRAFFE", 1))
        delete_session(proj, folder)
        floor.record()
        r = api.receipt()
        assert r["all_verified"] is False
        assert any("chunks match" in f["detail"] for f in r["files"])


class TestRestore:
    def test_restore_puts_the_whole_session_folder_back_byte_exact(self, floor):
        from brain_mcp.recorder import api

        proj, folder = make_session(floor)
        before = {p.relative_to(proj): p.read_bytes() for p in proj.rglob("*")
                  if p.is_file() and "memory" not in p.parts}
        floor.record()
        delete_session(proj, folder)
        r = api.restore(SID)
        assert r["ok"] and r["written"] == 4, r
        after = {p.relative_to(proj): p.read_bytes() for p in proj.rglob("*")
                 if p.is_file() and "memory" not in p.parts}
        assert after == before
        assert r["resume"] == f"cd /Users/test/proj && claude --resume {SID}"

    def test_restoring_identical_bytes_is_a_no_op(self, floor):
        from brain_mcp.recorder import api

        make_session(floor)
        floor.record()
        r = api.restore(SID)
        assert r["ok"] and r["written"] == 0
        assert all(f["action"] == "identical" for f in r["files"])

    def test_different_bytes_at_the_target_are_never_overwritten(self, floor):
        from brain_mcp.recorder import api

        proj, folder = make_session(floor)
        floor.record()
        main = proj / f"{SID}.jsonl"
        main.write_bytes(b'{"changed":"by hand"}\n')
        shutil.rmtree(folder)
        r = api.restore(SID)
        assert not r["ok"] and len(r["conflicts"]) == 1 and r["written"] == 0
        assert main.read_bytes() == b'{"changed":"by hand"}\n'
        assert not folder.exists(), "all-or-nothing: the folder was not restored either"

    def test_to_restores_elsewhere_mirroring_the_layout(self, floor, tmp_path):
        from brain_mcp.recorder import api

        proj, folder = make_session(floor)
        floor.record()
        r = api.restore(SID, to=str(tmp_path / "elsewhere"))
        assert r["ok"] and r["written"] == 4
        assert (tmp_path / "elsewhere" / "-Users-test" / f"{SID}.jsonl").read_bytes() == \
            (proj / f"{SID}.jsonl").read_bytes()
        assert (tmp_path / "elsewhere" / "-Users-test" / SID / "tool-results" / "toolu_9.txt").exists()

    def test_dry_run_writes_nothing(self, floor):
        from brain_mcp.recorder import api

        proj, folder = make_session(floor)
        floor.record()
        delete_session(proj, folder)
        r = api.restore(SID, dry_run=True)
        assert r["ok"] and r["written"] == 0 and len(r["files"]) == 4
        assert not (proj / f"{SID}.jsonl").exists()

    def test_a_redacted_copy_needs_explicit_consent(self, floor):
        from brain_mcp.recorder import api, cli

        proj, folder = make_session(floor)
        floor.record()
        cli.cmd_redact(argparse.Namespace(file=f"lake/cc_transcript/{SID}.jsonl",
                                          lines=[1, 1], reason="test"))
        delete_session(proj, folder)
        r = api.restore(SID)
        assert not r["ok"] and "redaction" in r["problems"][0]
        assert api.restore(SID, accept_redacted=True)["ok"]
