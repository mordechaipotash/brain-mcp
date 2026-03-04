"""
brain-mcp — Basic smoke tests.

Tests that core components work:
- Config loading
- Ingesters parse sample data correctly
- Schema is consistent
- Noise filter works
- MCP server starts without crashing (tool registration)
"""

import json
from pathlib import Path
from datetime import datetime

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestConfig:
    """Test configuration loading and validation."""

    def test_load_defaults(self):
        """Config loads with sensible defaults when no file exists."""
        from brain_mcp.config import BrainConfig

        cfg = BrainConfig()
        assert cfg.data_dir == Path("./data")
        assert cfg.vectors_dir == Path("./vectors")
        assert cfg.embedding.model == "nomic-ai/nomic-embed-text-v1.5"
        assert cfg.embedding.dim == 768
        assert cfg.server_name == "my-brain"
        assert len(cfg.domains) > 0

    def test_load_from_yaml(self, tmp_path):
        """Config loads from a YAML file correctly."""
        from brain_mcp.config import load_config

        yaml_content = """
data_dir: ./my_data
vectors_dir: ./my_vectors
embedding:
  model: test-model
  dim: 384
  batch_size: 25
domains:
  - test-domain-1
  - test-domain-2
server:
  name: test-brain
"""
        config_file = tmp_path / "brain.yaml"
        config_file.write_text(yaml_content)

        cfg = load_config(str(config_file))
        assert cfg.embedding.model == "test-model"
        assert cfg.embedding.dim == 384
        assert cfg.embedding.batch_size == 25
        assert cfg.server_name == "test-brain"
        assert cfg.domains == ["test-domain-1", "test-domain-2"]

    def test_path_resolution(self, tmp_path):
        """Relative paths resolve relative to config file location."""
        from brain_mcp.config import load_config

        yaml_content = """
data_dir: ./data
vectors_dir: ./vectors
"""
        config_file = tmp_path / "brain.yaml"
        config_file.write_text(yaml_content)

        cfg = load_config(str(config_file))
        assert cfg.data_dir == (tmp_path / "data").resolve()
        assert cfg.vectors_dir == (tmp_path / "vectors").resolve()

    def test_derived_paths(self):
        """Derived paths compute correctly from base directories."""
        from brain_mcp.config import BrainConfig

        cfg = BrainConfig(data_dir=Path("/test/data"),
                          vectors_dir=Path("/test/vectors"))
        assert cfg.parquet_path == Path("/test/data/all_conversations.parquet")
        assert cfg.lance_path == Path("/test/vectors/brain.lance")
        assert cfg.summaries_parquet == Path(
            "/test/data/brain_summaries_v6.parquet"
        )

    def test_env_var_brain_config(self, tmp_path, monkeypatch):
        """BRAIN_CONFIG env var points to config file."""
        from brain_mcp.config import load_config

        yaml_content = "server:\n  name: env-test\n"
        config_file = tmp_path / "custom.yaml"
        config_file.write_text(yaml_content)

        monkeypatch.setenv("BRAIN_CONFIG", str(config_file))
        cfg = load_config()
        assert cfg.server_name == "env-test"

    def test_source_config(self, tmp_path):
        """Sources parse correctly from YAML."""
        from brain_mcp.config import load_config

        yaml_content = """
sources:
  - type: claude-code
    path: ~/.claude/projects/
  - type: generic
    path: /tmp/conversations/
    name: my-custom
    format: jsonl
"""
        config_file = tmp_path / "brain.yaml"
        config_file.write_text(yaml_content)

        cfg = load_config(str(config_file))
        assert len(cfg.sources) == 2
        assert cfg.sources[0].type == "claude-code"
        assert cfg.sources[1].name == "my-custom"
        assert cfg.sources[1].format == "jsonl"

    def test_load_from_toml(self, tmp_path):
        """Config loads from a TOML file correctly."""
        from brain_mcp.config import load_config

        toml_content = b"""
data_dir = "./my_data"
vectors_dir = "./my_vectors"
domains = ["test-domain-1", "test-domain-2"]

[embedding]
model = "test-model"
dim = 384
batch_size = 25

[server]
name = "toml-brain"

[[sources]]
type = "claude-code"
path = "~/.claude/projects/"

[[sources]]
type = "generic"
path = "/tmp/conversations/"
name = "my-custom"
format = "jsonl"
"""
        config_file = tmp_path / "config.toml"
        config_file.write_bytes(toml_content)

        cfg = load_config(str(config_file))
        assert cfg.embedding.model == "test-model"
        assert cfg.embedding.dim == 384
        assert cfg.embedding.batch_size == 25
        assert cfg.server_name == "toml-brain"
        assert cfg.domains == ["test-domain-1", "test-domain-2"]
        assert len(cfg.sources) == 2
        assert cfg.sources[0].type == "claude-code"
        assert cfg.sources[1].name == "my-custom"

    def test_toml_preferred_over_yaml(self, tmp_path, monkeypatch):
        """When both config.toml and brain.yaml exist, TOML is preferred."""
        from brain_mcp.config import _find_config_path

        (tmp_path / "config.toml").write_bytes(b'[server]\nname = "toml-wins"\n')
        (tmp_path / "brain.yaml").write_text("server:\n  name: yaml-loses\n")

        monkeypatch.setenv("BRAIN_HOME", str(tmp_path))
        path = _find_config_path()
        assert path is not None
        assert path.name == "config.toml"

    def test_config_singleton(self):
        """get_config returns a singleton, set_config overrides it."""
        from brain_mcp.config import BrainConfig, set_config, get_config

        custom = BrainConfig(server_name="singleton-test")
        set_config(custom)
        assert get_config().server_name == "singleton-test"

        # Reset for other tests
        set_config(BrainConfig())

    def test_validate_config(self, tmp_path):
        """Config validation produces warnings for missing paths."""
        from brain_mcp.config import BrainConfig, validate_config

        cfg = BrainConfig(
            data_dir=tmp_path / "nonexistent",
            vectors_dir=tmp_path / "also-nonexistent",
        )
        warnings = validate_config(cfg)
        assert len(warnings) >= 1
        assert any("not found" in w or "does not exist" in w for w in warnings)


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEMA TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestSchema:
    """Test the canonical message schema."""

    def test_make_record(self):
        """make_record produces a valid record with all fields."""
        from brain_mcp.ingest.schema import make_record, SCHEMA_COLUMNS

        record = make_record(
            source="test",
            conversation_id="conv-1",
            role="user",
            content="Hello, how are you?",
            timestamp=datetime(2025, 1, 15, 10, 30, 0),
            msg_index=0,
            model="test-model",
            conversation_title="Test Chat",
        )

        assert record is not None
        # Check all schema columns are present
        for col in SCHEMA_COLUMNS:
            assert col in record, f"Missing column: {col}"

        assert record["source"] == "test"
        assert record["role"] == "user"
        assert record["word_count"] == 4
        assert record["has_question"] == 1
        assert record["year"] == 2025
        assert record["month"] == 1
        assert record["day_of_week"] == "Wednesday"

    def test_make_record_empty_content(self):
        """make_record returns None for empty content."""
        from brain_mcp.ingest.schema import make_record

        result = make_record(
            source="test",
            conversation_id="conv-1",
            role="user",
            content="",
            timestamp=datetime.now(),
        )
        assert result is None

    def test_make_record_code_detection(self):
        """make_record detects code blocks."""
        from brain_mcp.ingest.schema import make_record

        record = make_record(
            source="test",
            conversation_id="conv-1",
            role="assistant",
            content="Here's the code:\n```python\nprint('hello')\n```",
            timestamp=datetime.now(),
        )
        assert record["has_code"] == 1

    def test_make_record_url_detection(self):
        """make_record detects URLs."""
        from brain_mcp.ingest.schema import make_record

        record = make_record(
            source="test",
            conversation_id="conv-1",
            role="user",
            content="Check out https://example.com for more info",
            timestamp=datetime.now(),
        )
        assert record["has_url"] == 1

    def test_finalize_conversation(self):
        """finalize_conversation sets is_first/is_last correctly."""
        from brain_mcp.ingest.schema import make_record, finalize_conversation

        records = []
        for i in range(5):
            r = make_record(
                source="test",
                conversation_id="conv-1",
                role="user" if i % 2 == 0 else "assistant",
                content=f"Message number {i} with some content here",
                timestamp=datetime(2025, 1, 15, 10, i),
                msg_index=i,
            )
            if r:
                records.append(r)

        finalized = finalize_conversation(records)
        assert len(finalized) == 5
        assert finalized[0]["is_first"] == 1
        assert finalized[0]["is_last"] == 0
        assert finalized[-1]["is_first"] == 0
        assert finalized[-1]["is_last"] == 1
        assert all(r["conversation_msg_count"] == 5 for r in finalized)

    def test_content_truncation(self):
        """make_record caps content at 50K chars."""
        from brain_mcp.ingest.schema import make_record

        long_content = "x" * 60000
        record = make_record(
            source="test",
            conversation_id="conv-1",
            role="user",
            content=long_content,
            timestamp=datetime.now(),
        )
        assert len(record["content"]) == 50000


# ═══════════════════════════════════════════════════════════════════════════════
# NOISE FILTER TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoiseFilter:
    """Test the noise filter for embedding pipeline."""

    def test_noise_detected(self):
        """Known noise messages are filtered."""
        from brain_mcp.ingest.noise_filter import is_noise_message

        noise_messages = [
            "yes", "no", "ok", "continue", "cont",
            "thanks", "hi", "go for it", "do it",
            "nice", "cool", "done", "perfect", "sure",
            "try again", "more", "next", "all",
        ]
        for msg in noise_messages:
            assert is_noise_message(msg), f"Should be noise: '{msg}'"

    def test_meaningful_not_filtered(self):
        """Meaningful messages pass through the filter."""
        from brain_mcp.ingest.noise_filter import is_noise_message

        meaningful = [
            "How does async/await work in Python?",
            "Can you explain the bottleneck theory?",
            "I need help debugging this React component",
            "What's the difference between Docker and VMs?",
            "Let me think about this approach...",
        ]
        for msg in meaningful:
            assert not is_noise_message(msg), f"Should NOT be noise: '{msg}'"

    def test_single_char_is_noise(self):
        """Single characters are noise."""
        from brain_mcp.ingest.noise_filter import is_noise_message

        for c in "abcxyz":
            assert is_noise_message(c)

    def test_tool_result_is_noise(self):
        """Tool results are noise."""
        from brain_mcp.ingest.noise_filter import is_noise_message

        assert is_noise_message("[Tool Result]")


# ═══════════════════════════════════════════════════════════════════════════════
# INGESTER TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestIngesters:
    """Test that ingesters correctly parse sample data."""

    def test_clawdbot_ingester(self):
        """Clawdbot ingester parses sample JSONL correctly."""
        from brain_mcp.ingest.clawdbot import parse_clawdbot_session

        jsonl_path = FIXTURES / "sample_clawdbot.jsonl"
        records = parse_clawdbot_session(jsonl_path)

        assert len(records) == 4  # 2 user + 2 assistant messages
        assert records[0]["source"] == "clawdbot"
        assert records[0]["role"] == "user"
        assert "virtual environment" in records[0]["content"]
        assert records[0]["conversation_msg_count"] == 4

    def test_chatgpt_ingester(self):
        """ChatGPT ingester parses sample export correctly."""
        from brain_mcp.ingest.chatgpt import parse_chatgpt_export

        json_path = FIXTURES / "sample_chatgpt.json"
        records = parse_chatgpt_export(json_path)

        assert len(records) == 2  # 1 user + 1 assistant
        assert records[0]["source"] == "chatgpt"
        assert "capital of France" in records[0]["content"]

    def test_claude_code_ingester(self):
        """Claude Code ingester parses sample JSONL correctly."""
        from brain_mcp.ingest.claude_code import parse_jsonl_file

        jsonl_path = FIXTURES / "sample_claude_code.jsonl"
        records = parse_jsonl_file(jsonl_path)

        assert len(records) >= 2  # At least 2 messages (user + assistant)
        assert records[0]["source"] == "claude-code"

    def test_generic_ingester(self):
        """Generic ingester parses sample JSONL correctly."""
        from brain_mcp.ingest.generic import parse_generic_jsonl

        jsonl_path = FIXTURES / "sample_generic.jsonl"
        records = parse_generic_jsonl(jsonl_path, source_name="test")

        assert len(records) == 3
        assert records[0]["source"] == "test"
        assert records[0]["role"] == "user"
        assert "containerization" in records[0]["content"]

    def test_ingester_schema_consistency(self):
        """All ingesters produce records matching the canonical schema."""
        from brain_mcp.ingest.schema import SCHEMA_COLUMNS
        from brain_mcp.ingest.clawdbot import parse_clawdbot_session
        from brain_mcp.ingest.chatgpt import parse_chatgpt_export
        from brain_mcp.ingest.generic import parse_generic_jsonl

        sources = [
            parse_clawdbot_session(FIXTURES / "sample_clawdbot.jsonl"),
            parse_chatgpt_export(FIXTURES / "sample_chatgpt.json"),
            parse_generic_jsonl(
                FIXTURES / "sample_generic.jsonl", "test"
            ),
        ]

        for records in sources:
            assert len(records) > 0
            for record in records:
                for col in SCHEMA_COLUMNS:
                    assert col in record, (
                        f"Missing column '{col}' in {record['source']} "
                        f"record"
                    )


# ═══════════════════════════════════════════════════════════════════════════════
# MCP SERVER TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestMCPServer:
    """Test that the MCP server starts and registers tools correctly."""

    def test_server_creates(self):
        """Server creates without errors."""
        from brain_mcp.config import BrainConfig, set_config

        # Use a config that won't try to load real data
        cfg = BrainConfig(
            data_dir=Path("/tmp/brain-test-nonexistent"),
            vectors_dir=Path("/tmp/brain-test-nonexistent-v"),
        )
        set_config(cfg)

        from brain_mcp.server.server import create_server
        mcp = create_server()
        assert mcp is not None

        # Reset
        set_config(BrainConfig())

    def test_tools_registered(self):
        """All expected tools are registered."""
        from brain_mcp.config import BrainConfig, set_config

        cfg = BrainConfig(
            data_dir=Path("/tmp/brain-test-nonexistent"),
            vectors_dir=Path("/tmp/brain-test-nonexistent-v"),
        )
        set_config(cfg)

        from brain_mcp.server.server import create_server
        mcp = create_server()

        # The expected tool names (from original)
        expected_tools = [
            "search_conversations",
            "get_conversation",
            "conversations_by_date",
            "semantic_search",
            "search_summaries",
            "unified_search",
            "search_docs",
            "what_do_i_think",
            "alignment_check",
            "thinking_trajectory",
            "what_was_i_thinking",
            "brain_stats",
            "unfinished_threads",
            "tunnel_state",
            "dormant_contexts",
            "context_recovery",
            "tunnel_history",
            "switching_cost",
            "cognitive_patterns",
            "open_threads",
            "trust_dashboard",
            "list_principles",
            "get_principle",
            "github_search",
            "query_analytics",
        ]

        # Get registered tool names
        registered = set()
        if hasattr(mcp, "_tool_manager"):
            # FastMCP internal
            for tool in mcp._tool_manager.list_tools():
                registered.add(tool.name)
        elif hasattr(mcp, "list_tools"):
            for tool in mcp.list_tools():
                registered.add(tool.name)

        # Check each expected tool
        missing = []
        for tool_name in expected_tools:
            if registered and tool_name not in registered:
                missing.append(tool_name)

        if registered:
            assert not missing, (
                f"Missing tools: {missing}. "
                f"Registered: {sorted(registered)}"
            )

        # Reset
        set_config(BrainConfig())


# ═══════════════════════════════════════════════════════════════════════════════
# DB LAYER TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestDBLayer:
    """Test database connection layer."""

    def test_parse_json_field(self):
        """parse_json_field handles various inputs."""
        from brain_mcp.server.db import parse_json_field

        assert parse_json_field(None) == []
        assert parse_json_field("") == []
        assert parse_json_field('["a", "b"]') == ["a", "b"]
        assert parse_json_field("just a string") == ["just a string"]
        assert parse_json_field('{"key": "value"}') == ["{'key': 'value'}"]

    def test_conversations_missing_parquet(self):
        """get_conversations raises clear error when parquet is missing."""
        from brain_mcp.config import BrainConfig, set_config
        from brain_mcp.server.db import get_conversations

        # Reset cached connection
        import brain_mcp.server.db
        brain_mcp.server.db._conversations_db = None

        cfg = BrainConfig(
            data_dir=Path("/tmp/nonexistent-brain-data"),
        )
        set_config(cfg)

        with pytest.raises(FileNotFoundError) as exc_info:
            get_conversations()

        assert "ingest pipeline" in str(exc_info.value).lower() or \
               "not found" in str(exc_info.value).lower()

        # Reset
        brain_mcp.server.db._conversations_db = None
        set_config(BrainConfig())

    def test_lance_db_missing_returns_none(self):
        """get_lance_db returns None when vectors don't exist."""
        from brain_mcp.config import BrainConfig, set_config
        from brain_mcp.server.db import get_lance_db

        import brain_mcp.server.db
        brain_mcp.server.db._lance_db = None

        cfg = BrainConfig(
            vectors_dir=Path("/tmp/nonexistent-vectors"),
        )
        set_config(cfg)

        result = get_lance_db()
        assert result is None

        # Reset
        brain_mcp.server.db._lance_db = None
        set_config(BrainConfig())

    def test_summaries_missing_returns_none(self):
        """get_summaries_db returns None when summaries don't exist."""
        from brain_mcp.config import BrainConfig, set_config
        from brain_mcp.server.db import get_summaries_db

        import brain_mcp.server.db
        brain_mcp.server.db._summaries_db = None

        cfg = BrainConfig(
            data_dir=Path("/tmp/nonexistent-brain-data"),
        )
        set_config(cfg)

        result = get_summaries_db()
        assert result is None

        # Reset
        brain_mcp.server.db._summaries_db = None
        set_config(BrainConfig())

    def test_principles_missing_returns_empty(self):
        """get_principles returns empty dict when no file configured."""
        from brain_mcp.config import BrainConfig, set_config
        from brain_mcp.server.db import get_principles

        import brain_mcp.server.db
        brain_mcp.server.db._principles_data = None

        cfg = BrainConfig(principles_path=None)
        set_config(cfg)

        result = get_principles()
        assert result == {}

        # Reset
        brain_mcp.server.db._principles_data = None
        set_config(BrainConfig())

    def test_principles_loads_yaml(self, tmp_path):
        """get_principles loads a YAML principles file."""
        from brain_mcp.config import BrainConfig, set_config
        from brain_mcp.server.db import get_principles

        import brain_mcp.server.db
        brain_mcp.server.db._principles_data = None

        principles_file = tmp_path / "principles.yaml"
        principles_file.write_text("""
principles:
  focus:
    name: Deep Focus
    definition: Depth beats breadth.
""")

        cfg = BrainConfig(principles_path=principles_file)
        set_config(cfg)

        result = get_principles()
        assert "principles" in result
        assert "focus" in result["principles"]
        assert result["principles"]["focus"]["name"] == "Deep Focus"

        # Reset
        brain_mcp.server.db._principles_data = None
        set_config(BrainConfig())
