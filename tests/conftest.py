"""
brain-mcp — Test configuration.

With pip install -e ., the package is importable directly.
No sys.path hacks needed.
"""

import os
from pathlib import Path

import pytest


@pytest.fixture()
def floor(tmp_path, monkeypatch):
    """An isolated BRAIN_HOME whose every lane points inside tmp_path.

    The default lanes point at the real ~/.claude, ~/.codex and ~/.pi; a test that scans
    with one of them left unpinned silently records the developer's own history (2.1's
    cc_sessiondir lane did exactly that before this fixture existed). So: pin all of them,
    and refuse to run if any lane is still on the real home.
    """
    monkeypatch.setenv("BRAIN_HOME", str(tmp_path / "brainhome"))
    projects, codex, pi = tmp_path / "claude", tmp_path / "codex", tmp_path / "pi"
    for d in (projects, codex, pi):
        d.mkdir()

    from brain_mcp.recorder import floor_db, paths

    paths.ensure_layout(["cc_transcript", "codex_rollout", "pi_session", "cc_sessiondir"])
    floor_db.init_db()
    with floor_db.write_conn() as c:
        for lane, glob_ in (
            ("cc_transcript", projects / "**" / "*.jsonl"),
            ("cc_sessiondir", projects / "*" / "*" / "**" / "*"),
            ("codex_rollout", codex / "**" / "*.jsonl"),
            ("pi_session", pi / "**" / "*.jsonl"),
        ):
            c.execute("UPDATE floor.lanes SET root_glob = ? WHERE lane = ?", [str(glob_), lane])
        unpinned = c.execute("SELECT lane FROM floor.lanes WHERE root_glob LIKE '~%'").fetchall()
    assert not unpinned, f"lanes still pointing at the real home: {unpinned}"

    class Floor:
        home = Path(os.environ["BRAIN_HOME"])

        def __init__(self):
            self.projects, self.codex, self.pi = projects, codex, pi

        @staticmethod
        def record():
            """What the 60s scheduler runs: scan, refresh the index, notice what vanished."""
            from brain_mcp.recorder import api, derived
            from brain_mcp.recorder.scanner import scan_tick

            scan = scan_tick()
            return {"scan": scan, "derived": derived.refresh(), "noticed_gone": api.notice_gone()}

    return Floor()
