# Changelog

## [2.1.0] — 2026-10-04 — the receipt: what the agents deleted, and putting it back

### Added
- **The whole session, not just the transcript.** A Claude Code session is a folder:
  subagent and workflow transcripts, their `.meta.json`, and `tool-results/` live in
  `<project>/<session>/` beside the `.jsonl`. Through 2.0.0 none of it was recorded — the
  session pattern matched **0 of 1,231** subagent transcripts on the author's machine (72% of
  Claude Code's files). The new `cc_sessiondir` lane matches **1,231 of 1,231**, and **3,046 of
  3,288** files under project folders (the rest are `memory/` folders, not sessions). The lake
  mirrors the origin's layout: `lake/cc_sessiondir/<project>/<session>/...`. Non-line files
  are captured whole (they rarely end in a newline, so the line path would wait forever);
  a changed file opens a new generation and the old is kept. Scanner-only, like Codex: a
  60-second scan sits well inside the 30-day cleanup.
- **The receipt.** `brain_health` (and `brain-mcp health` / `doctor`) now report files whose
  origin is gone — deleted or moved — while the floor still holds them: *"1 session(s) and
  4 file(s) are gone from where the agent kept them; the floor holds every one."* `record`
  notices each one, re-hashes its floor bytes against every chunk the manifest recorded at
  capture, and logs an `origin_gone` event, so the dates are when the recorder noticed —
  measured — and the read-only MCP tool writes nothing. A tampered floor copy reports
  `verified: false`.
- **`brain-mcp restore <session>`** puts a session — transcript and folder — back where its
  agent will find it, byte-exact, then prints `cd <cwd> && claude --resume <id>`. All or
  nothing; never overwrites different bytes (identical is a no-op); `--dry-run`, `--to DIR`,
  `--list`; a copy carrying redaction tombstones needs `--accept-redacted`. CLI only — no MCP
  tool writes outside the floor. Proven end to end with a real Claude Code session that
  spawned a subagent and saved a tool output: deleted → `claude --resume` said "No
  conversation found" → `brain-mcp restore` wrote 4 files, each byte-identical to what
  Claude Code had written → the resumed session answered with its codeword and its
  subagent's reply.
- **`since` / `until`** on `brain_search` (a bare date for `until` includes the day). The
  abstention used to tell you to widen a date range that did not exist.

### Changed
- **`role="user"` means what the person wrote.** Text the harness injects under the user
  role — skill bodies, reminders, slash-command wrappers, task notifications, compaction
  summaries — is excluded by default (`include_machine=True` brings it back, which is the
  2.0.0 behaviour). On the author's 30 newest sessions that was **41.1%** of user rows. The
  Claude Code rule is `is_him()` from the author's m-plugin `hooks/lib/turns.py`, whose
  exclusions were earned by replaying transcripts; against it the new flag agrees on
  **880 of 894** rows, and all 14 differences are compaction summaries, which this release
  also treats as machine text (as the author's own corpus does). For Codex, injected context
  blocks (`<environment_context>`, `<recommended_plugins>`, `AGENTS.md`, …; 67% of the
  author's Codex user messages) are excluded, and a realtime voice delegation keeps only its
  spoken `<input>`.
- Subagent transcripts are recorded but kept out of default search, `brain_recent` and
  `brain_sessions` (`include_subagents=True` to include them): agent-to-agent work is not
  the person's conversation. A subagent hit belongs to its parent session and cites its own
  nested floor file.
- `brain_sessions` counts only the person's own turns.
- **Network, said plainly.** "Zero network calls at runtime" was false: building the index
  makes DuckDB download its `fts` extension once. `brain-mcp install` now does that fetch up
  front and says so; `doctor` reports whether it is cached. Still no HTTP client in the
  package.
- The scanner reads each lane's last-seen sizes once per tick instead of opening DuckDB per
  file: an unchanged tick over 3,046 files went from ~16 s to ~0.3 s (measured on 300 real
  files: 1.58 s → 0.028 s).

### Upgrading
- A 2.0.0 index gains three columns (`human_authored`, `is_subagent`, `lake_file`) on the
  first 2.1 `record`. Rows the floor can re-create are re-derived, so the new flags are
  computed, never defaulted; rows with no floor bytes behind them (migrate-v1) are kept with
  `human_authored = (role = 'user')`. Proven from a PyPI 2.0.0 floor: 11 → 14 columns, and
  `role="user"` on the test fixture went from 9 hits (machine text included) to 2.

### Fixed
- Caught by that upgrade proof: the new SQL carried regex escapes in a non-raw Python string
  (`SyntaxWarning: invalid escape sequence '\s'` on every fresh install's first run). CI now
  fails on any `SyntaxWarning`.

### Tests
- 47 (19 new): session-folder capture, human-authored text for Claude Code and Codex, the
  date window, the receipt (including a tampered copy and "reading writes nothing"), every
  restore rule, and the 2.0.0 upgrade. 18 of the 19 fail against 2.0.0; the upgrade test also
  fails against 2.1 with 2.0.0's copy-every-row reshape. A shared fixture pins every lane to
  a temp dir and refuses to run otherwise — the new lane had silently scanned the developer's
  real `~/.claude` in the old fixtures. Green on DuckDB 1.3.0, 1.5.6, 1.6.0.dev and 2.0.0.dev.

## [2.0.0] — 2026-10-04 — stable: every recorded agent is searchable, and upgrades keep every row

`pip install brain-mcp` and `uvx brain-mcp` now install the recorder. Until today they
installed 0.4.1, because every 2.x release was a pre-release; drop `--pre`, which also
let pip pick DuckDB development builds for you.

### Fixed
- **Codex and Pi messages were invisible to search** (PR #9, thanks @sidey79). Their
  branches of `derived.v_messages_all` had unnamed columns, so `UNION ALL BY NAME`
  left `msg_id`, `agent` and `text` empty and none of their rows reached the index.
- **DuckDB 1.6 broke `brain-mcp record`** (issue #8, thanks @sidey79). The text macro
  used the `x -> …` lambda syntax DuckDB 1.6 rejects; it now uses `lambda x: …`.
- **…and broke search a second way**, not in the report: DuckDB 1.6 refuses a WHERE
  that names a side-effecting SELECT alias, which `match_bm25 … AS rank` is. The score
  is now filtered outside a subquery.
- **Upgrading from 2.0.0b1–b3 would have broken `record`.** The same unnamed columns
  gave every b-series `derived.messages` 16 columns instead of 11. `refresh()` now
  reshapes the table to the view's columns on first run, copying every row (rows from
  `migrate-v1` have no floor bytes behind them and could not be re-derived), then
  rebuilds the FTS index. Inserts name their columns instead of relying on position.
- **`migrate-v1` failed on every b-series install** for the same reason; fixed by the
  same guard.
- **`install cc` missed capture hooks shipped by plugins** and could double-spool; it
  now checks `~/.claude/plugins/**/hooks/hooks.json` as well as `settings.json`.

### Changed
- `duckdb>=1.3,<2` (was `>=1.0`). Tested on 1.3.0, 1.5.6, 1.6.0.dev379 and 2.0.0.dev.
- Package description and keywords describe the recorder; the plugin pins
  `brain-mcp==2.0.0`.

### Tests
- `tests/test_derived_agents.py`: Claude Code, Codex and Pi all searchable; a planted
  b3-shaped table survives the upgrade with every row; `migrate-v1` on a fresh install;
  the derived SQL loads with single-arrow lambdas disabled. All seven fail against
  2.0.0b3, and the two upgrade tests fail against PR #9 without the reshape guard.

## [2.0.0b3] — 2026-08-20 — scan-secrets: the floor can now audit itself

### Added
- **`brain-mcp scan-secrets`** — finds credentials sitting in the floor, because a
  bytes-forever floor replicates whatever it captured into every backup. Reports a
  sha256 handle per finding, never a value, so the output is safe to paste anywhere
  and the id is stable across runs (that is how you confirm a rotation worked).
  `--exit-nonzero-on-findings` makes it cron-able. `brain-mcp doctor` now reports the count.
- Known documentation examples (AWS's canonical key) and same-line-annotated
  placeholders are excluded by value, not by guesswork.

### Note
Found on the author's own 767-session floor on release day: 50 distinct credentials.
The scanner's discrimination rules were both written by a falsification test that
caught them failing in each direction — see tests/test_secrets_scan.py.

## [2.0.0b2] — 2026-08-20 — fix: search crashed without pytz

b1's search/recent/sessions/health fetched TIMESTAMPTZ columns, and duckdb's Python
API requires pytz to materialize those — present transitively in every dev venv,
absent in a clean install. Caught by a stranger-test of the published wheel against
a real 767-session corpus. Fixed by casting timestamps to VARCHAR at the SQL
boundary — the mcp+duckdb-only dependency claim stays true.

## [2.0.0b1] — 2026-08-20 — THE RECORDER

A rebuild around one principle: capture the bytes first; derive everything else.

### Added
- **The floor**: `~/.brain/lake/<lane>/<session>.jsonl` — append-only session files,
  byte-identical to the originals, sha256 manifest, witness generations on rewrite.
- **Capture at source**: CC Stop/SessionEnd/PreCompact hooks (line-delta spool,
  store-and-forward drain) + a 60s poll-scanner for Codex and Pi. Shared identity —
  hook and scanner can never double-ingest.
- **7 MCP tools on the mcp 2.x SDK** (spec 2026-07-28): brain_search (cited-or-abstain),
  brain_get (sha-verified raw lines), brain_recent, brain_sessions, brain_health
  (unknown ≠ healthy), brain_capture_status, brain_backup (re-hash verified).
- **CLI**: record · install · uninstall · health · backup · redact · migrate-v1 · doctor · serve.
- **Claude Code plugin** (`plugin/`) shipping hooks + server in one install.

### Removed
- The legacy 25-tool server, dashboard, embed and summarize pipelines, the v1 CLI,
  and the bob-protocol wrapper (server_v1). v1 data imports via `migrate-v1`.
  A claim that cannot carry a line-span citation is not a claim this server makes.

## [1.0.0-beta.2] — 2026-08-20 — hotfix: unbroken installs, telemetry removed, CI green

### Fixed
- **Fresh installs crashed since 2026-07-28**: `mcp` 2.0.0 removed the `mcp.server.fastmcp`
  import path and the pin was unbounded. Now `mcp>=1.0,<2` (v2 will target the new SDK).
- **CI red since May**: top-level `yaml`/`pandas` imports fired on any package import even
  under the 2-dep install; now lazy. Test workflow installs `.[dev,legacy]`. 44/44 green.
- Version skew: `__version__` now matches the package version.

### Removed
- **Telemetry, entirely.** `brain_mcp/telemetry.py` is a no-op shim; the hardcoded ingest
  endpoint/key it carried is retired. Zero network calls at runtime.

### Added
- **Pi ingester** (`brain_mcp/ingest/pi.py`) — Pi (pi.ai coding agent) sessions from
  `~/.pi/agent/sessions/**/*.jsonl` now ingest like the other sources. Handles the v3
  format: `session` metadata → conversation_id/project, `message` lines → records
  (toolResult skipped), and models tracked from `model_change` events since Pi does not
  carry the model on the message itself. Closes #2. Thanks @pierre-mgmt for the
  format writeup.

## [1.0.0-beta.1] — 2026-05-28

**v1.0 BETA.** Major rewrite: brain-mcp is now a thin MCP server wrapping the
[Bob protocol](https://apiiam.com/bob/init.0). Storage moved from cloud Postgres
(SHELET ref-impl) to local parquets under `~/.bob/`.

Pre-release. To install: `pip install brain-mcp --pre`. To stay on v0.4.0:
`pip install brain-mcp` (default — no `--pre` flag).

### Changed
- **Storage**: `~/.bob/turns.parquet` (Bob protocol output) replaces hosted Supabase.
- **Tools**: 9 read-only tools (`bob_health`, `bob_search`, `bob_recent`,
  `bob_conversations_by_date`, `bob_tunnel_state`, `bob_what_do_i_think`,
  `bob_thinking_trajectory`, `bob_open_threads`, `bob_dropped`) replace the
  25 SHELET-skill surface.
- **Dependencies**: only `mcp` + `duckdb`. v0.x deps (lancedb, pandas, pyarrow,
  fastembed, anthropic, fastapi, jinja2) moved to optional `legacy` extras.
- **Pitch**: "memory belongs to the rememberer is architecture, not morality."

### Removed
- Cloud Supabase connection (no more anon keys / DB URLs in env).
- Semantic search (v1.0 is keyword-only; embeddings are a separate future protocol).
- brainmcp.dev hosted dashboard dependency.

### Compatibility
- v0.x users staying on `brain-mcp==0.4.0` are unaffected. No auto-upgrade.
- v0.x env vars (`BRAIN_MCP_DB_URL`, `BRAIN_MCP_API_KEY`) are now ignored —
  remove them when upgrading.
- v0.x CLI (`brain_mcp.cli:main`) is no longer the script entry point. To
  use it: `python -m brain_mcp.cli` (legacy import path preserved).

### Upgrade path
1. Install Bob (the protocol that produces `~/.bob/turns.parquet`): paste
   `https://apiiam.com/bob/init.0` into any MCP-aware LLM session.
2. `pip install brain-mcp --pre` (or `uv tool install brain-mcp --pre`).
3. Register: `{"brain": {"command": "uvx", "args": ["brain-mcp", "--pre"]}}` in
   `~/.claude/mcp.json` (or your client's MCP config).

---


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
