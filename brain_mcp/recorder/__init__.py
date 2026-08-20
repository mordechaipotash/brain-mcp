"""brain-mcp v2 — THE RECORDER.

Capture at source (hook where offered, watch where not), one spool, one floor
of raw bytes + sha. The lake directory is the floor of record; DuckDB is a
re-derivable cache. Nothing here imports the legacy v0/v1 stacks.

Spec: canon/2026-08-20/BRAIN-MCP-V2-BUILD-SPEC.md (the-big-great-rebuild vault),
preflight-backed 2026-08-20 (§8b): session-assembled lake files (P1), DuckDB
open-write-close per tick (P2), scanner-only Codex (P4), delta-primary (P5).
"""
