# brain 2.1.1 — one session id at two paths is two files, not one flip-flopping file.
"""Renaming a project folder can leave the same session uuid under two project
directories, each copy with lines the other lacks. Through 2.1.0 the recorder keyed a
file by its session id, so every 60-second tick saw "the other" copy as a rewrite and
wrote a full new generation: on the author's Mac, 2026-08-24, about 2,256 copies of 5
sessions, 73 GB in four days. Doctrine: duplicates are sightings — keep both, by path."""

import shutil
from pathlib import Path

FIX = Path(__file__).parent / "fixtures"
SID = "dddddddd-1111-2222-3333-444444444444"
A, B = "-Users-test-sot", "-Users-test-zman"   # A sorts first: it keeps the bare key


def line(text, ts="2026-09-02T10:00:00Z"):
    return ('{"type":"user","message":{"role":"user","content":"%s"},"timestamp":"%s"}\n'
            % (text, ts)).encode()


def two_copies(floor):
    base = (FIX / "sample_cc_mixed.jsonl").read_bytes()
    for proj, extra in ((A, line("only in sot: the walrus")),
                        (B, line("only in zman: the narwhal") + line("and a second zman line"))):
        d = floor.projects / proj
        d.mkdir()
        (d / f"{SID}.jsonl").write_bytes(base + extra)
    return floor.projects / A / f"{SID}.jsonl", floor.projects / B / f"{SID}.jsonl"


def lake_files(floor):
    d = floor.home / "lake" / "cc_transcript"
    return sorted(p.name for p in d.iterdir() if p.is_file())


def rewrites(floor):
    from brain_mcp.recorder.floor_db import read_conn

    with read_conn() as c:
        return c.execute("SELECT count(*) FROM floor.witnesses WHERE growth = 'rewrite'").fetchone()[0]


class TestDuplicateSessionIds:
    def test_twenty_ticks_write_no_new_generations(self, floor):
        a, b = two_copies(floor)
        for _ in range(20):
            floor.record()
        assert lake_files(floor) == [f"{SID}.jsonl", f"{SID}~{B}.jsonl"], lake_files(floor)
        assert rewrites(floor) == 0
        lake = floor.home / "lake" / "cc_transcript"
        assert (lake / f"{SID}.jsonl").read_bytes() == a.read_bytes()
        assert (lake / f"{SID}~{B}.jsonl").read_bytes() == b.read_bytes()

    def test_both_copies_are_one_session_in_search(self, floor):
        from brain_mcp.recorder import api

        two_copies(floor)
        floor.record()
        walrus = api.search("walrus")["hits"][0]["citation"]
        narwhal = api.search("narwhal")["hits"][0]["citation"]
        assert walrus["session_id"] == narwhal["session_id"] == SID
        assert walrus["file"] == f"lake/cc_transcript/{SID}.jsonl"
        assert narwhal["file"] == f"lake/cc_transcript/{SID}~{B}.jsonl"
        assert api.get(narwhal["file"], narwhal["lines"], expect_sha256=narwhal["sha256"])["verified"] is True

    def test_restore_puts_each_copy_back_in_its_own_folder(self, floor):
        from brain_mcp.recorder import api

        a, b = two_copies(floor)
        want = {a: a.read_bytes(), b: b.read_bytes()}
        floor.record()
        a.unlink(); b.unlink()
        r = api.restore(SID)
        assert r["ok"] and r["written"] == 2, r
        assert {p: p.read_bytes() for p in want} == want

    def test_a_moved_session_keeps_its_identity(self, floor):
        from brain_mcp.recorder import api
        from brain_mcp.recorder.floor_db import read_conn

        src = floor.projects / A
        src.mkdir()
        (src / f"{SID}.jsonl").write_bytes((FIX / "sample_cc_mixed.jsonl").read_bytes())
        floor.record()
        dst = floor.projects / B
        dst.mkdir()
        shutil.move(src / f"{SID}.jsonl", dst / f"{SID}.jsonl")
        with open(dst / f"{SID}.jsonl", "ab") as f:
            f.write(line("written after the move"))
        floor.record()
        assert lake_files(floor) == [f"{SID}.jsonl"] and rewrites(floor) == 0
        with read_conn() as c:
            assert c.execute("SELECT abs_path FROM floor.files WHERE lane='cc_transcript'").fetchone()[0] \
                == str(dst / f"{SID}.jsonl")
        assert api.receipt()["files_gone"] == 0, "a move is not a deletion"


class TestRewriteCap:
    def test_a_file_that_keeps_rewriting_stops_at_the_cap(self, floor):
        from brain_mcp.recorder import api, visit
        from brain_mcp.recorder.scanner import scan_tick

        d = floor.projects / A
        d.mkdir()
        f = d / f"{SID}.jsonl"
        f.write_bytes(line("generation zero"))
        floor.record()
        verdicts = []
        for i in range(1, 9):   # each pass truncates to different bytes: a rewrite every time
            f.write_bytes(line(f"rewrite number {i} " + "x" * i))
            verdicts.append(scan_tick()["cc_transcript"])
        gens = [n for n in lake_files(floor) if n.startswith(SID)]
        assert len(gens) == 1 + visit.REWRITE_CAP, gens
        assert any("rewrite-capped" in v for v in verdicts)
        lane = next(l for l in api.health()["lanes"] if l["lane"] == "cc_transcript")
        assert lane["verdict"] == "degraded" and "rewrite" in lane["reason"]
        events = (floor.home / "manifest" / "manifest.jsonl").read_bytes().count(b'"event":"rewrite_capped"')
        assert events == 1, "one event per file per day, not one per tick"


class TestHookRouting:
    def test_a_spool_delta_names_its_project(self, floor):
        from brain_mcp.recorder.drain import drain_tick
        from brain_mcp.recorder.paths import spool_dir

        a, b = two_copies(floor)
        floor.record()
        n = b.read_bytes().count(b"\n")
        new = line("a turn the hook saw in zman")
        with open(b, "ab") as fh:
            fh.write(new)
        (spool_dir("cc_transcript") / f"{SID}@{B}.turn-{n}-{n + 1}.jsonl").write_bytes(new)
        assert drain_tick().get("applied_delta") == 1
        lake = floor.home / "lake" / "cc_transcript"
        assert (lake / f"{SID}~{B}.jsonl").read_bytes() == b.read_bytes()
        assert (lake / f"{SID}.jsonl").read_bytes() == a.read_bytes(), "the other copy is untouched"

    def test_old_spool_names_still_drain_to_the_first_copy(self, floor):
        from brain_mcp.recorder.drain import drain_tick
        from brain_mcp.recorder.paths import spool_dir

        a, _ = two_copies(floor)
        floor.record()
        n = a.read_bytes().count(b"\n")
        new = line("a turn from an older hook")
        with open(a, "ab") as fh:
            fh.write(new)
        (spool_dir("cc_transcript") / f"{SID}.turn-{n}-{n + 1}.jsonl").write_bytes(new)
        assert drain_tick().get("applied_delta") == 1
        assert (floor.home / "lake" / "cc_transcript" / f"{SID}.jsonl").read_bytes() == a.read_bytes()


class TestRunawayDetection:
    def test_doctor_finds_files_with_many_generations_and_touches_nothing(self, floor, monkeypatch):
        from brain_mcp.recorder import api, visit

        monkeypatch.setattr(visit, "REWRITE_CAP", 1000)   # let a runaway happen, as in 2.1.0
        d = floor.projects / A
        d.mkdir()
        f = d / f"{SID}.jsonl"
        for i in range(13):
            f.write_bytes(line(f"copy {i} " + "y" * i))
            floor.record()
        lake = floor.home / "lake" / "cc_transcript"
        before = {p.name: p.read_bytes() for p in lake.iterdir()}
        rep = api.runaway_report(min_generations=10)
        assert rep["files"][0]["generations"] == 13 and rep["files"][0]["bytes"] > 0
        assert {p.name: p.read_bytes() for p in lake.iterdir()} == before
