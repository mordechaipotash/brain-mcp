"""Tests for dashboard Day 4-5: onboarding, tools, settings pages."""

import json
import importlib
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

pytest.importorskip("fastapi", reason="Dashboard tests require fastapi")
from fastapi.testclient import TestClient

from brain_mcp.dashboard.app import create_app


@pytest.fixture
def client():
    """Create a test client for the dashboard app."""
    app = create_app()
    return TestClient(app)


# ═══════════════════════════════════════════════════════════════════════════════
# ONBOARDING TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestOnboardingPages:
    """Test onboarding wizard page loads."""

    def test_onboarding_page_loads(self, client):
        """Onboarding page should load without error."""
        response = client.get("/onboarding")
        assert response.status_code == 200
        assert "Setup" in response.text
        assert "Step 1" in response.text or "step" in response.text.lower()
        assert "Discover" in response.text or "discover" in response.text.lower()

    def test_onboarding_has_all_steps(self, client):
        """Onboarding page should mention all 5 steps."""
        response = client.get("/onboarding")
        assert response.status_code == 200
        assert "Step 1" in response.text
        assert "Step 2" in response.text
        assert "Step 3" in response.text
        assert "Step 4" in response.text
        assert "Step 5" in response.text

    def test_onboarding_has_alpine(self, client):
        """Onboarding should use Alpine.js for state."""
        response = client.get("/onboarding")
        assert response.status_code == 200
        assert "x-data" in response.text
        assert "onboardingWizard" in response.text


class TestOnboardingAPI:
    """Test onboarding API endpoints."""

    def test_onboarding_status(self, client):
        """Status endpoint should return JSON with complete flag."""
        response = client.get("/api/onboarding/status")
        assert response.status_code == 200
        data = response.json()
        assert "complete" in data
        assert "current_step" in data
        assert isinstance(data["complete"], bool)

    def test_onboarding_complete(self, client, tmp_path, monkeypatch):
        """Complete endpoint should mark onboarding done."""
        # Point state file to temp dir
        state_path = tmp_path / "onboarding.json"
        monkeypatch.setattr(
            "brain_mcp.dashboard.routes.onboarding.ONBOARDING_STATE_PATH",
            state_path,
        )

        response = client.post("/api/onboarding/complete")
        assert response.status_code == 200
        data = response.json()
        assert data["complete"] is True

        # Verify state was persisted
        saved = json.loads(state_path.read_text())
        assert saved["complete"] is True

    def test_onboarding_step_update(self, client, tmp_path, monkeypatch):
        """Step endpoint should update current step."""
        state_path = tmp_path / "onboarding.json"
        monkeypatch.setattr(
            "brain_mcp.dashboard.routes.onboarding.ONBOARDING_STATE_PATH",
            state_path,
        )

        response = client.post("/api/onboarding/step/3")
        assert response.status_code == 200
        data = response.json()
        assert data["current_step"] == 3

    def test_onboarding_step_clamps(self, client, tmp_path, monkeypatch):
        """Step values should be clamped to 1-5."""
        state_path = tmp_path / "onboarding.json"
        monkeypatch.setattr(
            "brain_mcp.dashboard.routes.onboarding.ONBOARDING_STATE_PATH",
            state_path,
        )

        response = client.post("/api/onboarding/step/99")
        assert response.json()["current_step"] == 5

        response = client.post("/api/onboarding/step/0")
        assert response.json()["current_step"] == 1

    def test_mcp_config_returns_json(self, client):
        """MCP config endpoint should return valid config."""
        response = client.get("/api/onboarding/mcp-config")
        assert response.status_code == 200
        data = response.json()
        assert "mcpServers" in data
        assert "brain-mcp" in data["mcpServers"]
        assert "command" in data["mcpServers"]["brain-mcp"]

    def test_mcp_config_snippet_html(self, client):
        """MCP config snippet should return HTML with JSON."""
        response = client.get("/api/onboarding/mcp-config/snippet")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        assert "mcpServers" in response.text

    def test_auto_configure_unknown_target(self, client):
        """Auto-configure with unknown target should fail gracefully."""
        response = client.post(
            "/api/onboarding/auto-configure",
            json={"target": "nonexistent-editor"},
        )
        assert response.status_code == 400

    def test_configure_embedding(self, client, tmp_path, monkeypatch):
        """Embedding config should save provider choice."""
        state_path = tmp_path / "onboarding.json"
        monkeypatch.setattr(
            "brain_mcp.dashboard.routes.onboarding.ONBOARDING_STATE_PATH",
            state_path,
        )

        response = client.post(
            "/api/onboarding/configure-embedding",
            json={"provider": "local"},
        )
        assert response.status_code == 200
        assert response.json()["provider"] == "local"

    def test_configure_summaries(self, client, tmp_path, monkeypatch):
        """Summary config should save provider choice."""
        state_path = tmp_path / "onboarding.json"
        monkeypatch.setattr(
            "brain_mcp.dashboard.routes.onboarding.ONBOARDING_STATE_PATH",
            state_path,
        )

        response = client.post(
            "/api/onboarding/configure-summaries",
            json={"provider": "gemini"},
        )
        assert response.status_code == 200
        assert response.json()["provider"] == "gemini"


class TestOnboardingDiscover:
    """Test the /api/onboarding/discover endpoint."""

    def test_discover_returns_list(self, client):
        """Discover endpoint should return a JSON list."""
        response = client.get("/api/onboarding/discover")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_discover_item_shape(self, client, tmp_path):
        """Each discovered source should have source_type, display_name, paths."""
        # Create a fake claude-code directory so at least one source is found
        fake_projects = tmp_path / ".claude" / "projects" / "test"
        fake_projects.mkdir(parents=True)
        (fake_projects / "session.jsonl").write_text("{}")

        with patch(
            "brain_mcp.dashboard.routes.onboarding._WELL_KNOWN_PATHS",
            [("claude-code", "Claude Code", tmp_path / ".claude" / "projects", "*.jsonl")],
        ), patch(
            "brain_mcp.dashboard.routes.onboarding._DISCOVER_MODULES",
            {},
        ):
            response = client.get("/api/onboarding/discover")
            assert response.status_code == 200
            data = response.json()
            assert len(data) >= 1

            item = data[0]
            assert "source_type" in item
            assert "display_name" in item
            assert "paths" in item
            assert isinstance(item["paths"], list)
            assert len(item["paths"]) >= 1
            assert "path" in item["paths"][0]
            assert "count_hint" in item["paths"][0]

    def test_discover_uses_registry_when_populated(self, client):
        """Discover should prefer the ingester registry when it has results."""
        fake_registry_results = {
            "test-source": [{"path": "/fake/path", "size": 42}],
        }
        mock_ingester = MagicMock()
        mock_ingester.display_name = "Test Source"

        with patch(
            "brain_mcp.dashboard.routes.onboarding.discover_all",
            return_value=fake_registry_results,
            create=True,
        ) as mock_discover_all, patch(
            "brain_mcp.ingest.registry.discover_all",
            return_value=fake_registry_results,
        ), patch(
            "brain_mcp.ingest.registry.get_ingester",
            return_value=mock_ingester,
        ):
            # Call the internal function directly to bypass import caching
            from brain_mcp.dashboard.routes.onboarding import _discover_sources

            results = _discover_sources()
            assert len(results) >= 1
            found = [r for r in results if r["source_type"] == "test-source"]
            assert len(found) == 1
            assert found[0]["display_name"] == "Test Source"
            assert found[0]["paths"][0]["path"] == "/fake/path"
            assert found[0]["paths"][0]["count_hint"] == 42

    def test_discover_fallback_module_discover(self, client, tmp_path):
        """Discover should fall back to module-level discover() functions."""
        fake_results = [{"path": "/tmp/fake.json", "size": 100}]

        fake_mod = MagicMock()
        fake_mod.discover = MagicMock(return_value=fake_results)

        with patch(
            "brain_mcp.ingest.registry.discover_all",
            return_value={},
        ), patch(
            "brain_mcp.dashboard.routes.onboarding._DISCOVER_MODULES",
            {"test-mod": ("fake_module", "Test Module")},
        ), patch(
            "brain_mcp.dashboard.routes.onboarding._WELL_KNOWN_PATHS",
            [],
        ), patch(
            "importlib.import_module",
            return_value=fake_mod,
        ):
            from brain_mcp.dashboard.routes.onboarding import _discover_sources

            results = _discover_sources()
            assert len(results) == 1
            assert results[0]["source_type"] == "test-mod"
            assert results[0]["display_name"] == "Test Module"
            assert results[0]["paths"][0]["count_hint"] == 100

    def test_discover_well_known_paths(self, client, tmp_path):
        """Discover should check well-known paths for ingesters without discover()."""
        fake_base = tmp_path / "agents"
        fake_base.mkdir()
        (fake_base / "a.jsonl").write_text("{}")
        (fake_base / "b.jsonl").write_text("{}")

        with patch(
            "brain_mcp.ingest.registry.discover_all",
            return_value={},
        ), patch(
            "brain_mcp.dashboard.routes.onboarding._DISCOVER_MODULES",
            {},
        ), patch(
            "brain_mcp.dashboard.routes.onboarding._WELL_KNOWN_PATHS",
            [("clawdbot", "Clawdbot", fake_base, "*.jsonl")],
        ):
            from brain_mcp.dashboard.routes.onboarding import _discover_sources

            results = _discover_sources()
            assert len(results) == 1
            assert results[0]["source_type"] == "clawdbot"
            assert results[0]["display_name"] == "Clawdbot"
            assert results[0]["paths"][0]["count_hint"] == 2

    def test_discover_empty_when_nothing_found(self, client):
        """Discover should return empty list if no sources exist."""
        with patch(
            "brain_mcp.ingest.registry.discover_all",
            return_value={},
        ), patch(
            "brain_mcp.dashboard.routes.onboarding._DISCOVER_MODULES",
            {},
        ), patch(
            "brain_mcp.dashboard.routes.onboarding._WELL_KNOWN_PATHS",
            [],
        ):
            response = client.get("/api/onboarding/discover")
            assert response.status_code == 200
            assert response.json() == []

    def test_discover_survives_import_errors(self, client):
        """Discover should not crash if an ingester module fails to import."""
        with patch(
            "brain_mcp.ingest.registry.discover_all",
            side_effect=ImportError("no registry"),
        ), patch(
            "brain_mcp.dashboard.routes.onboarding._DISCOVER_MODULES",
            {"broken": ("nonexistent.module", "Broken")},
        ), patch(
            "brain_mcp.dashboard.routes.onboarding._WELL_KNOWN_PATHS",
            [],
        ):
            response = client.get("/api/onboarding/discover")
            assert response.status_code == 200
            assert response.json() == []


# ═══════════════════════════════════════════════════════════════════════════════
# TOOLS PAGE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestToolsPage:
    """Test tool status page and API."""

    def test_tools_page_loads(self, client):
        """Tools page should load without error."""
        response = client.get("/tools")
        assert response.status_code == 200
        assert "Tool Status" in response.text
        assert "Test All" in response.text

    def test_list_tools_json(self, client):
        """Tools list should return 25 tools as JSON."""
        response = client.get("/api/tools")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 25

        # Check structure
        tool = data[0]
        assert "name" in tool
        assert "description" in tool
        assert "category" in tool
        assert "status" in tool
        assert "status_icon" in tool
        assert "requires" in tool

    def test_tools_have_all_categories(self, client):
        """Tools should span all 7 categories."""
        response = client.get("/api/tools")
        data = response.json()
        categories = set(t["category"] for t in data)
        expected = {
            "Cognitive Prosthetic", "Search", "Synthesis",
            "Conversation", "GitHub", "Analytics", "Meta",
        }
        assert categories == expected

    def test_tool_category_counts(self, client):
        """Category counts should match spec."""
        response = client.get("/api/tools")
        data = response.json()
        counts = {}
        for t in data:
            counts[t["category"]] = counts.get(t["category"], 0) + 1
        assert counts["Cognitive Prosthetic"] == 8
        assert counts["Search"] == 6
        assert counts["Synthesis"] == 4
        assert counts["Conversation"] == 3
        assert counts["GitHub"] == 1
        assert counts["Analytics"] == 1
        assert counts["Meta"] == 2

    def test_tool_status_values(self, client):
        """Tool status should be one of ok, degraded, unavailable."""
        response = client.get("/api/tools")
        data = response.json()
        valid_statuses = {"ok", "degraded", "unavailable"}
        for tool in data:
            assert tool["status"] in valid_statuses, f"{tool['name']}: {tool['status']}"

    def test_tool_cards_html(self, client):
        """Tool cards should return HTML partial."""
        response = client.get("/api/tools/cards")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        assert "Cognitive Prosthetic" in response.text
        assert "Search" in response.text

    def test_test_unknown_tool(self, client):
        """Testing unknown tool should return error HTML."""
        response = client.get("/api/tools/nonexistent_tool/test")
        assert response.status_code == 200
        assert "Unknown tool" in response.text

    def test_test_all_tools(self, client):
        """Test-all should start a background task."""
        response = client.post("/api/tools/test-all")
        assert response.status_code == 200
        assert "test" in response.text.lower() or "progress" in response.text.lower()

    def test_run_unknown_tool(self, client):
        """Running unknown tool should return error."""
        response = client.post(
            "/api/tools/nonexistent_tool/run",
            json={"query": "test"},
        )
        assert response.status_code == 200
        assert "Unknown tool" in response.text


class TestToolRegistry:
    """Test the tool registry data structure."""

    def test_all_tools_have_required_fields(self):
        """Every tool should have name, description, category, requires."""
        from brain_mcp.dashboard.routes.tools import TOOLS

        for tool in TOOLS:
            assert "name" in tool, f"Tool missing name: {tool}"
            assert "description" in tool, f"Tool missing description: {tool['name']}"
            assert "category" in tool, f"Tool missing category: {tool['name']}"
            assert "requires" in tool, f"Tool missing requires: {tool['name']}"

    def test_tool_names_unique(self):
        """All tool names should be unique."""
        from brain_mcp.dashboard.routes.tools import TOOLS

        names = [t["name"] for t in TOOLS]
        assert len(names) == len(set(names)), f"Duplicate names: {[n for n in names if names.count(n) > 1]}"

    def test_check_data_available(self):
        """Data availability checker should return dict of bools."""
        from brain_mcp.dashboard.routes.tools import _check_data_available

        available = _check_data_available()
        assert isinstance(available, dict)
        for key in ["conversations", "embeddings", "summaries", "github", "principles"]:
            assert key in available
            assert isinstance(available[key], bool)

    def test_tool_status_logic(self):
        """Tool status should correctly reflect data availability."""
        from brain_mcp.dashboard.routes.tools import _tool_status

        # Tool with no requirements = always ok
        assert _tool_status({"requires": []}, {}) == "ok"

        # Tool with all requirements met = ok
        assert _tool_status(
            {"requires": ["conversations"]},
            {"conversations": True},
        ) == "ok"

        # Tool with some requirements met = degraded
        assert _tool_status(
            {"requires": ["conversations", "summaries"]},
            {"conversations": True, "summaries": False},
        ) == "degraded"

        # Tool with no requirements met = unavailable
        assert _tool_status(
            {"requires": ["embeddings"]},
            {"embeddings": False},
        ) == "unavailable"


# ═══════════════════════════════════════════════════════════════════════════════
# SETTINGS PAGE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestSettingsPage:
    """Test settings page and API."""

    def test_settings_page_loads(self, client):
        """Settings page should load without error."""
        response = client.get("/settings")
        assert response.status_code == 200
        assert "Settings" in response.text

    def test_get_settings_json(self, client):
        """Settings endpoint should return JSON."""
        response = client.get("/api/settings")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)

    def test_settings_cards_html(self, client):
        """Settings cards should return HTML partial."""
        response = client.get("/api/settings/cards")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        assert "Data Directory" in response.text or "Embedding" in response.text

    def test_disk_usage(self, client):
        """Disk usage should return breakdown."""
        response = client.get("/api/settings/disk")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)
        # Should have total at minimum
        if "error" not in data:
            assert "total" in data

    def test_embedding_status(self, client):
        """Embedding status should return counts."""
        response = client.get("/api/settings/embedding-status")
        assert response.status_code == 200
        data = response.json()
        assert "total" in data
        assert "embedded" in data
        assert "percent" in data

    def test_summary_status(self, client):
        """Summary status should return counts."""
        response = client.get("/api/settings/summary-status")
        assert response.status_code == 200
        data = response.json()
        assert "total" in data
        assert "summarized" in data
        assert "percent" in data

    def test_cron_status(self, client):
        """Cron status should return installed flag."""
        response = client.get("/api/settings/cron")
        assert response.status_code == 200
        data = response.json()
        assert "installed" in data
        assert isinstance(data["installed"], bool)

    def test_validate_key_empty(self, client):
        """Empty key should return invalid."""
        response = client.post(
            "/api/settings/validate-key",
            json={"key": ""},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False

    def test_validate_key_no_body(self, client):
        """Missing body should be handled."""
        response = client.post(
            "/api/settings/validate-key",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        # Should not crash
        assert response.status_code in (200, 400)

    def test_mcp_config_settings(self, client):
        """Settings MCP config should return HTML."""
        response = client.get("/api/settings/mcp-config")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        assert "mcpServers" in response.text


class TestSettingsHelpers:
    """Test settings helper functions."""

    def test_deep_merge(self):
        """Deep merge should combine dicts recursively."""
        from brain_mcp.dashboard.routes.settings import _deep_merge

        base = {"a": 1, "b": {"c": 2, "d": 3}}
        update = {"b": {"c": 99}, "e": 5}
        result = _deep_merge(base, update)

        assert result["a"] == 1
        assert result["b"]["c"] == 99
        assert result["b"]["d"] == 3
        assert result["e"] == 5

    def test_deep_merge_overwrites_non_dict(self):
        """Deep merge should overwrite non-dict values."""
        from brain_mcp.dashboard.routes.settings import _deep_merge

        base = {"a": {"b": 1}}
        update = {"a": "replaced"}
        result = _deep_merge(base, update)
        assert result["a"] == "replaced"

    def test_format_size(self):
        """Format size should produce human-readable strings."""
        from brain_mcp.dashboard.routes.settings import _format_size

        assert _format_size(500) == "500 B"
        assert "KB" in _format_size(5000)
        assert "MB" in _format_size(5_000_000)
        assert "GB" in _format_size(5_000_000_000)

    def test_dir_size_nonexistent(self):
        """Dir size of nonexistent path should return 0."""
        from brain_mcp.dashboard.routes.settings import _dir_size

        assert _dir_size(Path("/nonexistent/path/xyz")) == 0

    def test_dir_size_file(self, tmp_path):
        """Dir size of a file should return its size."""
        from brain_mcp.dashboard.routes.settings import _dir_size

        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")
        size = _dir_size(test_file)
        assert size == 11  # len("hello world")

    def test_dir_size_directory(self, tmp_path):
        """Dir size of a directory should sum all files."""
        from brain_mcp.dashboard.routes.settings import _dir_size

        (tmp_path / "a.txt").write_text("aaaa")  # 4 bytes
        (tmp_path / "b.txt").write_text("bb")  # 2 bytes
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "c.txt").write_text("ccc")  # 3 bytes

        size = _dir_size(tmp_path)
        assert size == 9


class TestSettingsUpdate:
    """Test config update (PUT /api/settings)."""

    def test_put_settings_invalid_json(self, client):
        """Invalid JSON should return 400."""
        response = client.put(
            "/api/settings",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400
