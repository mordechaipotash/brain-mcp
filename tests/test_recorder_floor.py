# brain v2 — floor round-trip tests: scanner visits, hook drain, citation contract.
"""Every test runs in an isolated BRAIN_HOME. The contract under test is P1's:
lake file byte-identical to origin prefix; citations verify with sed+shasum
semantics (span bytes re-hash); rewrite opens a new generation, old kept."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "sample_claude_code.jsonl"
SESSION = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_HOME", str(tmp_path / "brainhome"))
    origin_root = tmp_path / "projects" / "-Users-test"
    origin_root.mkdir(parents=True)
    origin = origin_root / f"{SESSION}.jsonl"
    shutil.copy(FIXTURE, origin)

    from brain_mcp.recorder import floor_db, paths

    paths.ensure_layout(["cc_transcript"])
    floor_db.init_db()
    with floor_db.write_conn() as c:
        c.execute(
            "UPDATE floor.lanes SET root_glob = ? WHERE lane = 'cc_transcript'",
            [str(origin_root / "**/*.jsonl")],
        )
    return {"origin": origin, "home": Path(os.environ["BRAIN_HOME"])}


def _lake(home) -> Path:
    return home["home"] / "lake" / "cc_transcript" / f"{SESSION}.jsonl"


def _scan():
    from brain_mcp.recorder.scanner import scan_tick

    return scan_tick(only_lane="cc_transcript")


class TestScannerVisits:
    def test_first_visit_byte_identity(self, home):
        census = _scan()
        assert census["cc_transcript"].get("first") == 1
        assert _lake(home).read_bytes() == home["origin"].read_bytes()

    def test_unchanged_then_append(self, home):
        _scan()
        assert _scan()["cc_transcript"].get("unchanged") == 1
        with open(home["origin"], "ab") as f:
            f.write(b'{"type":"user","message":{"role":"user","content":"appended line"},"timestamp":"2026-08-20T10:00:00Z"}\n')
        assert _scan()["cc_transcript"].get("append") == 1
        assert _lake(home).read_bytes() == home["origin"].read_bytes()

    def test_torn_trailing_line_waits(self, home):
        _scan()
        with open(home["origin"], "ab") as f:
            f.write(b'{"type":"user","torn": tr')  # no newline
        census = _scan()
        assert census["cc_transcript"].get("torn-wait") == 1
        # lake must NOT contain the torn bytes
        assert not _lake(home).read_bytes().endswith(b"tr")

    def test_rewrite_opens_new_generation(self, home):
        _scan()
        content = home["origin"].read_bytes()
        home["origin"].write_bytes(b'{"rewritten":"yes"}\n' + content[50:])
        census = _scan()
        assert census["cc_transcript"].get("rewrite") == 1
        g2 = home["home"] / "lake" / "cc_transcript" / f"{SESSION}.g2.jsonl"
        assert g2.exists(), "generation-2 lake file"
        assert _lake(home).exists(), "generation-1 KEPT (supersede, never delete)"

    def test_raw_lines_and_manifest_written(self, home):
        _scan()
        from brain_mcp.recorder.floor_db import read_conn

        with read_conn() as c:
            n = c.execute("SELECT count(*) FROM floor.raw_lines").fetchone()[0]
            nn = c.execute(
                "SELECT count(*) FROM floor.raw_lines WHERE captured_at IS NULL"
            ).fetchone()[0]
        assert n == len(FIXTURE.read_bytes().splitlines())
        assert nn == 0, "captured_at NOT NULL, ever"
        manifest = home["home"] / "manifest" / "manifest.jsonl"
        entries = [json.loads(l) for l in manifest.read_text().splitlines()]
        assert entries and all(e["v"] == 1 for e in entries)
        assert entries[0]["chunk_sha256"]


class TestCitationContract:
    def test_sed_shasum_verifies_and_survives_append(self, home):
        _scan()
        lf = _lake(home)
        lines = lf.read_bytes().splitlines(keepends=True)
        a, b = 1, min(2, len(lines))
        span = b"".join(lines[a - 1:b])
        cite_sha = hashlib.sha256(span).hexdigest()
        out = subprocess.run(
            f"sed -n '{a},{b}p' '{lf}' | shasum -a 256",
            shell=True, capture_output=True, text=True,
        ).stdout.split()[0]
        assert out == cite_sha, "citation verifies with sed+shasum, no database"
        with open(home["origin"], "ab") as f:
            f.write(b'{"more":"data"}\n')
        _scan()
        out2 = subprocess.run(
            f"sed -n '{a},{b}p' '{lf}' | shasum -a 256",
            shell=True, capture_output=True, text=True,
        ).stdout.split()[0]
        assert out2 == cite_sha, "span-sha stable under append"


class TestHookDrain:
    def _spool(self, home) -> Path:
        return home["home"] / "spool" / "cc_transcript"

    def test_delta_applies_then_snapshot_dedups(self, home):
        lines = FIXTURE.read_bytes().splitlines(keepends=True)
        cut = len(lines) // 2
        sp = self._spool(home)
        # hook ships lines 1..cut as the first delta
        (sp / f"{SESSION}.turn-0-{cut}.jsonl").write_bytes(b"".join(lines[:cut]))
        from brain_mcp.recorder.drain import drain_tick

        census = drain_tick()
        assert census.get("applied_delta") == 1
        assert not list(sp.glob("*.jsonl")), "confirm-then-delete drained the spool"
        # second delta extends to the end
        (sp / f"{SESSION}.turn-{cut}-{len(lines)}.jsonl").write_bytes(b"".join(lines[cut:]))
        assert drain_tick().get("applied_delta") == 1
        assert _lake(home).read_bytes() == b"".join(lines)
        # final snapshot arrives — entirely known content
        (sp / f"{SESSION}.jsonl").write_bytes(b"".join(lines))
        assert drain_tick().get("discarded_snapshot_known") == 1

    def test_gap_defers_until_scanner_catches_up(self, home):
        lines = FIXTURE.read_bytes().splitlines(keepends=True)
        sp = self._spool(home)
        # a delta starting at line 5 arrives before anything else (gap)
        (sp / f"{SESSION}.turn-5-{len(lines)}.jsonl").write_bytes(b"".join(lines[5:]))
        from brain_mcp.recorder.drain import drain_tick

        assert drain_tick().get("deferred_gap") == 1
        assert list(sp.glob("*.jsonl")), "gap artifact stays in spool"
        _scan()  # scanner ingests the full origin
        assert drain_tick().get("discarded_dup") == 1
        assert not list(sp.glob("*.jsonl"))

    def test_hook_and_scanner_share_one_identity(self, home):
        """The bug this test exists for: drain and scanner must converge on ONE
        file_id per session or the floor double-ingests."""
        lines = FIXTURE.read_bytes().splitlines(keepends=True)
        cut = len(lines) // 2
        (self._spool(home) / f"{SESSION}.turn-0-{cut}.jsonl").write_bytes(b"".join(lines[:cut]))
        from brain_mcp.recorder.drain import drain_tick
        from brain_mcp.recorder.floor_db import read_conn

        drain_tick()
        _scan()  # scanner sees the full origin; must CONTINUE, not restart
        with read_conn() as c:
            fids = c.execute("SELECT count(DISTINCT file_id) FROM floor.raw_lines").fetchone()[0]
            n = c.execute("SELECT count(*) FROM floor.raw_lines WHERE witness_gen=1").fetchone()[0]
        assert fids == 1, "one identity per session across hook+scanner"
        assert n == len(lines), "no double-ingest"
        assert _lake(home).read_bytes() == b"".join(lines)
