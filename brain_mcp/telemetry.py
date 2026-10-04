"""brain-mcp telemetry — REMOVED in 0.4.1.

Telemetry was removed entirely (and the endpoint it posted to was retired).
This module remains as a no-op shim so existing imports keep working.
brain-mcp ships no HTTP client. Its one network fetch is DuckDB downloading its
full-text-search extension (fts) on first use — done up front by `brain-mcp install`.
"""

from __future__ import annotations

from typing import Any, Optional


def is_enabled() -> bool:
    return False


def set_enabled(enabled: bool) -> None:
    return None


def track(event: str, props: Optional[dict[str, Any]] = None) -> None:
    return None


def track_tool(tool_name: str, latency_ms: float, result_count: int = 0,
               error_type: Optional[str] = None) -> None:
    return None


def track_error(tool_name: str, error_type: str) -> None:
    return None


def flush() -> None:
    return None


def maybe_show_notice() -> None:
    return None
