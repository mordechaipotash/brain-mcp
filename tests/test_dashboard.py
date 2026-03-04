"""Tests for the dashboard search functionality."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from brain_mcp.dashboard.app import create_app


@pytest.fixture
def client():
    """Create a test client for the dashboard app."""
    app = create_app()
    return TestClient(app)


class TestDashboardPages:
    """Test that HTML pages load without errors."""

    def test_home_page(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "Brain MCP" in response.text

    def test_search_page(self, client):
        response = client.get("/search")
        assert response.status_code == 200
        assert "Search Your Brain" in response.text
        assert "semantic" in response.text.lower()
        assert "keyword" in response.text.lower()
        assert "summaries" in response.text.lower()

    def test_sources_page(self, client):
        response = client.get("/sources")
        assert response.status_code == 200

    def test_tools_page(self, client):
        response = client.get("/tools")
        assert response.status_code == 200

    def test_settings_page(self, client):
        response = client.get("/settings")
        assert response.status_code == 200


class TestSearchAPI:
    """Test the /api/search endpoint."""

    def test_empty_query_returns_empty(self, client):
        """Empty query should return empty state."""
        response = client.get("/api/search?q=&mode=semantic")
        assert response.status_code == 200
        # Empty state — no results, no error

    def test_keyword_search_returns_html(self, client):
        """Keyword search should return HTML partial."""
        response = client.get("/api/search?q=test&mode=keyword")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")

    def test_search_with_all_filters(self, client):
        """Search with all filters should work."""
        response = client.get(
            "/api/search?q=test&mode=keyword"
            "&source=clawdbot&role=user"
            "&date_from=2024-01-01&date_to=2026-12-31"
        )
        assert response.status_code == 200

    def test_invalid_mode_defaults(self, client):
        """Invalid mode should not crash."""
        response = client.get("/api/search?q=test&mode=invalid")
        assert response.status_code == 200


class TestSearchHistory:
    """Test search history persistence."""

    def test_recent_searches_loads(self, client):
        """Recent searches endpoint should return HTML."""
        response = client.get("/api/search/recent")
        assert response.status_code == 200

    def test_search_filters_endpoint(self, client):
        """Filters endpoint should return JSON."""
        response = client.get("/api/search/filters")
        assert response.status_code == 200


class TestConversationViewer:
    """Test conversation viewer page."""

    def test_nonexistent_conversation(self, client):
        """Non-existent conversation should show error."""
        response = client.get("/conversation/nonexistent-id-12345")
        assert response.status_code == 200
        assert "Not Found" in response.text or "not found" in response.text.lower() or "Error" in response.text

    def test_conversation_with_highlight(self, client):
        """Conversation with highlight param should load."""
        response = client.get("/conversation/nonexistent-id?highlight=test")
        assert response.status_code == 200


class TestSearchHelpers:
    """Test search helper functions."""

    def test_similarity_class(self):
        from brain_mcp.dashboard.routes.search import _similarity_class
        assert _similarity_class(0.9) == "sim-high"
        assert _similarity_class(0.8) == "sim-high"
        assert _similarity_class(0.7) == "sim-med"
        assert _similarity_class(0.5) == "sim-low"

    def test_similarity_dot(self):
        from brain_mcp.dashboard.routes.search import _similarity_dot
        assert "🟢" in _similarity_dot(0.9)
        assert "🟡" in _similarity_dot(0.7)
        assert "🔴" in _similarity_dot(0.4)

    def test_truncate(self):
        from brain_mcp.dashboard.routes.search import _truncate
        assert _truncate("short") == "short"
        long_text = "word " * 100
        result = _truncate(long_text, 50)
        assert len(result) <= 55  # 50 + room for "..."
        assert result.endswith("...")

    def test_guess_source(self):
        from brain_mcp.dashboard.routes.search import _guess_source
        assert _guess_source("cc_local_abc123") == "claude-code"
        assert _guess_source("chatgpt_early_abc") == "chatgpt"
        assert _guess_source("cb_something") == "clawdbot"
        assert _guess_source("unknown_id") == "unknown"
        assert _guess_source("") == "unknown"
