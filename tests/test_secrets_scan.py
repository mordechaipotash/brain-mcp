# brain v2 — the credential scanner must be able to FAIL, and must discriminate.
"""These tests exist because the first version of _is_example could not
recognize the placeholders it was written to skip (it checked only the matched
substring), and the first fix over-corrected — a 60-char window let an
unrelated nearby "fake" suppress a REAL planted secret. Both directions are
pinned here: catch the real one, skip the annotated one, never print a value.
"""

import os
import shutil

import pytest

GHP = "ghp_" + "z" * 36
SBP = "sbp_" + "a" * 40
AWS_DOCS_EXAMPLE = "AKIAIOSFODNN7EXAMPLE"


@pytest.fixture()
def floor(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_HOME", str(tmp_path / "bh"))
    from brain_mcp.recorder import floor_db, paths

    paths.ensure_layout(["cc_transcript"])
    floor_db.init_db()
    lake = paths.lake_dir("cc_transcript")
    lake.mkdir(parents=True, exist_ok=True)
    return lake


def _scan():
    from brain_mcp.recorder import secrets

    return secrets.scan()


class TestScannerDiscriminates:
    def test_catches_a_planted_secret(self, floor):
        (floor / "a.jsonl").write_text('{"token":"%s"}\n' % GHP)
        r = _scan()
        assert r["distinct_findings"] == 1
        assert r["by_shape"]["github_pat"] == 1

    def test_ignores_the_aws_documentation_example(self, floor):
        (floor / "a.jsonl").write_text('{"k":"%s"}\n' % AWS_DOCS_EXAMPLE)
        assert _scan()["distinct_findings"] == 0

    def test_ignores_a_same_line_annotated_placeholder(self, floor):
        (floor / "a.jsonl").write_text('{"k":"%s","note":"fake placeholder"}\n' % SBP)
        assert _scan()["distinct_findings"] == 0

    def test_nearby_fake_does_NOT_suppress_a_real_secret(self, floor):
        """The over-correction guard: 'fake' on a DIFFERENT line must not
        silence a real finding. Proximity is not evidence; sharing a line is."""
        (floor / "a.jsonl").write_text(
            '{"real":"%s"}\n{"other":"%s","note":"this is a fake placeholder"}\n' % (GHP, SBP)
        )
        r = _scan()
        assert r["by_shape"].get("github_pat") == 1, "the real secret must survive"
        assert r["distinct_findings"] == 1

    def test_never_prints_a_value(self, floor):
        (floor / "a.jsonl").write_text('{"token":"%s"}\n' % GHP)
        import json

        blob = json.dumps(_scan())
        assert GHP not in blob, "a scan report must be safe to paste anywhere"
        assert len(_scan()["findings"][0]["id"]) == 12  # sha prefix, not the value

    def test_same_secret_gets_a_stable_id_across_runs(self, floor):
        (floor / "a.jsonl").write_text('{"token":"%s"}\n' % GHP)
        first = _scan()["findings"][0]["id"]
        (floor / "b.jsonl").write_text('{"again":"%s"}\n' % GHP)
        r = _scan()
        assert r["distinct_findings"] == 1, "one secret in two files is ONE finding"
        assert r["findings"][0]["id"] == first, "ids must be stable to confirm a rotation"
        assert len(r["findings"][0]["files"]) == 2
