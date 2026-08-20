"""brain-mcp v2 MCP server — a thin wrapper: 7 tools over recorder.api.

Targets mcp>=2 (spec 2026-07-28): MCPServer (FastMCP path is deleted), typed
dict returns -> structuredContent, stderr logging only (MCP Logging is
deprecated), stateless, deterministic tool order. api.py holds all logic.
"""

from __future__ import annotations

from typing import Any, Optional

from mcp.server.mcpserver import MCPServer

from . import api

mcp = MCPServer(
    "brain-mcp",
    title="brain — the recorder",
    instructions=(
        "Local recorder for AI conversation history (Claude Code, Codex, Pi). "
        "Everything returned is CITED (file + line span + sha256, verifiable via "
        "brain_get) or ABSTAINED (with the coverage actually searched). An "
        "abstention means 'not found in the lanes and dates measured', never "
        "'it did not happen'. Health verdicts: 'unknown' means unmeasured, not "
        "healthy — never summarize an unknown lane as fine."
    ),
)


@mcp.tool(title="Search recorded history (cited or abstained)",
          annotations={"readOnlyHint": True})
def brain_search(query: str, agent: Optional[str] = None, role: Optional[str] = None,
                 limit: int = 10, min_rank: float = 0.0, order: str = "rank") -> dict[str, Any]:
    """Keyword (BM25) search over all recorded conversations. Every hit carries a
    mechanically verifiable citation; zero hits return an explicit abstention with
    coverage. role='user' answers 'what did I say'; order='time_asc' reads a
    thinking trajectory."""
    return api.search(query, agent=agent, role=role, limit=limit,
                      min_rank=min_rank, order=order)


@mcp.tool(title="Fetch + verify the raw lines behind a citation",
          annotations={"readOnlyHint": True})
def brain_get(file: str, lines: list[int], expect_sha256: Optional[str] = None,
              context: int = 0) -> dict[str, Any]:
    """Raw floor bytes for a cited span. Pass the citation's sha256 as
    expect_sha256 to verify; a mismatch is reported, never repaired."""
    return api.get(file, lines, expect_sha256=expect_sha256, context=context)


@mcp.tool(title="Recent activity across all recorded agents",
          annotations={"readOnlyHint": True})
def brain_recent(hours: int = 24, agent: Optional[str] = None,
                 role: Optional[str] = None, limit: int = 20) -> dict[str, Any]:
    """Time-ordered recent messages, every row cited. An empty result states its
    coverage — silence never renders as an answer."""
    return api.recent(hours=hours, agent=agent, role=role, limit=limit)


@mcp.tool(title="Browse recorded sessions", annotations={"readOnlyHint": True})
def brain_sessions(date: Optional[str] = None, agent: Optional[str] = None,
                   limit: int = 50) -> dict[str, Any]:
    """Session cards (agent, span, message counts, floor file) for a date or
    range. A whole session's raw text is brain_get(file, [1, n])."""
    return api.sessions(date=date, agent=agent, limit=limit)


@mcp.tool(title="Per-lane freshness — is the data current?",
          annotations={"readOnlyHint": True})
def brain_health() -> dict[str, Any]:
    """Origin-vs-floor staleness per lane. fresh = floor holds everything the
    agent's own files show; stale = the agent has content the recorder missed;
    unknown = unmeasured, NOT healthy (mandatory reason attached)."""
    return api.health()


@mcp.tool(title="Is the capture machinery itself running?",
          annotations={"readOnlyHint": True})
def brain_capture_status() -> dict[str, Any]:
    """Spool depth, heartbeat file ages (side-effect mtimes, never self-report),
    floor totals. Distinct from health: a backing-up spool is invisible to
    health until it becomes staleness."""
    return api.capture_status()


@mcp.tool(title="Back up the floor (writes only outside it)")
def brain_backup(dest: str) -> dict[str, Any]:
    """Sync lake/ + manifest/ to dest (pure-add, content-addressed), then verify
    by re-hashing sampled files at the destination. The verify can fail — that
    is the point."""
    return api.backup(dest)


def main() -> None:  # pragma: no cover
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
