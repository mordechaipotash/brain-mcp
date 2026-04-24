# Changelog

All notable changes to Brain MCP will be documented in this file.

## [0.4.0] — 2026-04-24 — SHELET reference implementation

This release reframes brain-mcp as the first **SHELET-compliant MCP server**. Every tool now declares the layer it operates on, what it reads, what it writes, and what citations it must return. See [ADR-001](docs/adr/001-shelet-reference-implementation.md) for the full rationale.

### Added
- **`.claude/skills/` pack** — 25 SKILL.md manifests, one per MCP tool, stratified across L0 (raw accounting) / L1 (deterministic retrieval) / L2 (synthesis with citations required) / L3 (fusion / route-to-attention) / utility
- **`make verify-skills`** — new Makefile target + `scripts/verify_skills.py` that validates every manifest against 8 invariants (required fields, layer/citations consistency, body sections). Wired into GitHub Actions CI alongside pytest.
- **SHELET citation helper** — `_cite(source_id, ts)` in `brain_mcp/server/tools_prosthetic.py` produces canonical `[source_id · YYYY-MM-DD]` markers. Rollout started on `context_recovery`, `tunnel_state` (Sources footer), and `what_do_i_think` (per-decision / per-question / per-quote citations).
- **Supabase Migration 003** — `supabase/migrations/003_shelet_l0_to_l3.sql` ships the L0-L3 canonical schema with CHECK-enforced citations, layer-bounded RLS policies, and `brain.resolve_citations(l3_id)` recursive citation-chain resolver. Optional layer, off by default. See [ADR-002](docs/adr/002-supabase-canonical-backend.md).
- **ADR-001** — full decision record for the SHELET adoption (context, stratification table, 7-day implementation sprint, consequences)
- **ADR-002** — Supabase canonical backend plan (migration ships now, Python adapter deferred to v0.5.0)

### Fixed
- **Critical launch blocker**: `brain_mcp/summarize/summarize.py` no longer reads the enhanced-extraction prompt from `../../../clawd/cogro/prompts/enhanced-extraction-v5.txt`. The prompt now ships inside the package at `brain_mcp/_prompts/enhanced-extraction-v5.txt` and is loaded via `importlib.resources`. Public installs no longer fail on first `brain-mcp summarize` with `FileNotFoundError`. Legacy cogro sibling path is kept as a third-tier fallback for backwards compatibility.
- `pyproject.toml` adds `"brain_mcp" = ["_prompts/*.txt"]` to `[tool.setuptools.package-data]` so the prompt ships with the wheel.

### Changed
- **r/mcp launch post rewritten** — new framing: "the first SHELET-compliant MCP server. 25 stratified skills, structural citation discipline, layer-bounded permissions." Links to ADR-001 and Migration 003.
- Package description: "Turn your AI conversations into a searchable second brain with cognitive prosthetic tools" → "SHELET-compliant cognitive prosthetic for AI agents — 25 stratified MCP skills with structural citation discipline"

### Deferred to v0.5.0
- Python Supabase adapter (`brain_mcp/supabase_adapter.py`)
- `brain-mcp setup --supabase` CLI flag
- Full citation rollout across remaining L2/L3 tools (`thinking_trajectory`, `dormant_contexts`, `open_threads`, `switching_cost`, `alignment_check`, `cognitive_patterns`)

## [0.1.9] — 2026-03-04

### Added — Dashboard (feature-complete)
- **Home page**: Live stats cards, activity sparkline, sync status, recent searches, source overview, domain threads
- **Search page**: 3 modes (semantic/keyword/summaries), debounced input, filters (source/role/date), conversation viewer with highlighting, search history, load-more pagination
- **Sources page**: Auto-discovery, source cards with stats, sync-all, per-source re-ingest, SSE progress streaming
- **Onboarding wizard**: 5-step Alpine.js stepper (discover → ingest → embedding → summaries → connect), MCP config generation, auto-configure for Claude/Cursor
- **Tool status page**: 25 tools grouped by 7 categories, health detection across 5 data layers, individual + batch testing with latency, interactive tool runner, fix suggestions for degraded tools
- **Settings page**: Config management (TOML read/write), disk usage, embedding/summary status bars, API key validation, cron install/remove/status, MCP config export
- **Background task system**: TaskManager with SSE streaming, thread-safe updates, used across sync/test operations
- 100 tests (58 core + 42 dashboard), all passing

## [0.1.8] — 2026-03-04

### Added
- `brain-mcp version` command
- `brain-mcp summarize` command (with guided setup if not configured)
- `brain-mcp dashboard` command (placeholder for v0.2.0)
- Claude Desktop auto-discovery in `brain-mcp init`
- Dashboard-first UX: running `brain-mcp` with no args opens dashboard

### Fixed
- Test assertion for server_name after v0.1.7 rename
- Removed orphaned `config.py` and `architecture.html` from repo

### Changed
- Default behavior: `brain-mcp` (no subcommand) → opens dashboard instead of printing help

## [0.1.7] — 2026-03-04

### Changed
- Renamed MCP server from `brain` to `my-brain` to avoid collisions with user configs

## [0.1.6] — 2026-03-03

### Fixed
- `brain-mcp init --full` crash when embedding not installed
- Config key collision with other MCP servers (now uses `brain-mcp` key)

## [0.1.5] — 2026-03-03

### Changed
- Embedding is now fully optional — `pip install brain-mcp[embed]` for semantic search
- Better UX: clear messages when optional features aren't installed

### Fixed
- Missing `pytz` dependency

## [0.1.4] — 2026-03-02

### Fixed
- Claude Code config path: uses `~/.claude.json` (not `~/.claude/mcp.json`)

## [0.1.3] — 2026-03-02

### Fixed
- Claude Code and Desktop setup paths
- Added `pipx install` as recommended install method

## [0.1.2] — 2026-03-01

### Fixed
- Claude Desktop/Code config path detection
- Safer embedding pipeline (handles missing model gracefully)

## [0.1.1] — 2026-03-01

### Fixed
- Missing `einops` dependency for embedding model

## [0.1.0] — 2026-03-01

### Added
- **25 MCP tools** across 7 categories: search, conversations, synthesis, stats, prosthetic, GitHub, analytics
- **4 conversation ingesters**: Claude Code, ChatGPT, Clawdbot, Generic JSONL
- **Local embedding pipeline** using nomic-embed-text-v1.5 (768-dim vectors)
- **LanceDB vector search** for semantic similarity queries
- **DuckDB SQL** over parquet for fast keyword search and analytics
- **Cognitive prosthetic tools**: tunnel_state, context_recovery, switching_cost, dormant_contexts, open_threads, cognitive_patterns, tunnel_history, trust_dashboard
- **Synthesis tools**: what_do_i_think, alignment_check, thinking_trajectory, what_was_i_thinking
- **Optional LLM summarization** (Anthropic, OpenAI, Ollama) for structured conversation analysis
- **Progressive feature tiers**: works with just conversations, improves with embeddings, and again with summaries
- **CLI** with init, ingest, embed, serve, setup, doctor, status, sync commands
- **Auto-discovery** of Claude Code and ChatGPT conversations
- **npx support** for zero-install usage
- **Configurable principles** for alignment_check (YAML format)
- 100% local — no telemetry, no cloud, no phone-home
