# brain-mcp — Pi ingester tests (issue #2)
"""Tests for the Pi (pi.ai coding agent) ingester."""

from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


class TestPiIngester:
    """Pi ingester parses the observed v3 session format correctly."""

    def test_parses_sample_session(self):
        from brain_mcp.ingest.pi import parse_jsonl_file

        records = parse_jsonl_file(FIXTURES / "sample_pi.jsonl")

        # 1 user + 2 assistant. toolResult, custom, thinking_level_change,
        # and the <15-char user "ok" are all skipped.
        assert len(records) == 3
        assert all(r["source"] == "pi" for r in records)
        assert [r["role"] for r in records] == ["user", "assistant", "assistant"]

    def test_session_metadata_mapping(self):
        from brain_mcp.ingest.pi import parse_jsonl_file

        records = parse_jsonl_file(FIXTURES / "sample_pi.jsonl")

        # conversation_id from the session line's id
        assert records[0]["conversation_id"] == "pi_11111111-2222-3333-4444-555555555555"
        # project derived from the session cwd's basename
        assert records[0]["project"] == "my-project"
        # exact timestamps come from each message line
        assert records[0]["temporal_precision"] == "exact"
        assert records[0]["conversation_msg_count"] == 3

    def test_model_tracked_from_model_change_events(self):
        """The model is NOT on .message — it must be carried from model_change lines."""
        from brain_mcp.ingest.pi import parse_jsonl_file

        records = parse_jsonl_file(FIXTURES / "sample_pi.jsonl")

        assert records[1]["model"] == "claude-sonnet-5"
        # a later model_change re-stamps subsequent messages
        assert records[2]["model"] == "claude-opus-5"

    def test_tool_results_and_noise_skipped(self):
        from brain_mcp.ingest.pi import parse_jsonl_file

        records = parse_jsonl_file(FIXTURES / "sample_pi.jsonl")

        joined = " ".join(r["content"] for r in records)
        assert "must be skipped" not in joined  # toolResult content
        # tool_use blocks inside assistant messages are dropped, text kept
        assert "refactor the parser" in joined

    def test_registered_in_plugin_registry(self):
        import brain_mcp.ingest  # noqa: F401 — triggers @register imports
        from brain_mcp.ingest.registry import get_ingester

        ing = get_ingester("pi")
        assert ing is not None
        assert ing.display_name == "Pi"

    def test_ingest_missing_dir_returns_empty(self):
        from brain_mcp.ingest.pi import ingest

        assert ingest("/nonexistent/path/for/test") == []
