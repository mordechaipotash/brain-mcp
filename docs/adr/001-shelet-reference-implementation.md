# ADR-001: brain-mcp as SHELET Reference Implementation

**Status**: Proposed
**Date**: 2026-04-24
**Deciders**: Mordechai Potash
**Tags**: governance, architecture, launch-strategy

---

## Context

brain-mcp currently ships 25 MCP tools over three data layers (DuckDB-backed Parquet for raw conversations, LanceDB for vector embeddings, LanceDB+Parquet for structured summaries). The tools work. But they lack:

1. **Declarative contracts.** Each tool is a Python function with a docstring. There is no machine-readable manifest describing what layer it operates on, what it reads, what it writes, or what guarantees it offers.
2. **Structural citations.** Tool outputs (especially the 8 prosthetic tools — `tunnel_state`, `context_recovery`, `what_do_i_think`, etc.) are synthesized Markdown prose. A reader cannot trace a claim back to a specific conversation without re-querying.
3. **Layer-bounded permissions.** Every tool has full access to every data store. There is no structural barrier preventing an L3 synthesis tool from accidentally writing to L0 raw storage.
4. **An external contract file** (`cogro/prompts/enhanced-extraction-v5.txt`) that breaks on public installs because the path assumes a sibling repository.

In parallel, `/Users/mordechai/viter-workspace/.claude/skills/` has evolved over the last two weeks (2026-04-20 through 2026-04-24) into a working stratified skills architecture for client-engagement context. Its L3 index skill explicitly states it "mirrors Brain MCP's three-tier architecture." The isomorphism is already recognized; what's missing is the closing of the loop back to brain-mcp itself.

The SHELET protocol (Stratified Human-Engaged Leverage Enhancement Technology), which Mordechai has developed since June 2025, provides the governance model: L0 immutable, each layer a pure function of the one below, every higher-layer claim carries a citation to the layer below.

## Decision

**Adopt SHELET as the governance model for brain-mcp.** Implement via three concrete changes:

### 1. Ship `.claude/skills/` alongside brain-mcp

Each of the 25 MCP tools gets a corresponding `SKILL.md` manifest declaring:

- `layer: L0 | L1 | L2 | L3 | utility`
- `reads: [L0.foo, L1.bar, ...]`
- `writes: [L2.baz, ...]` (empty for read-only tools)
- `citations: required | output-is-citation | not-required`
- `determinism: pure-function | temporal | LLM-guided`
- `allowed-tools:` (MCP tool names it wraps)

The manifest is the declarative contract. The Python function is the implementation. The MCP server reads the manifest at startup and validates tool invocations against the declared permissions.

Natural stratification of the existing 25 tools:

| Layer | Tools |
|---|---|
| L0 (raw accounting) | brain_stats, trust_dashboard, get_conversation |
| L1 (deterministic retrieval) | semantic_search, search_conversations, search_summaries, search_docs, unified_search, conversations_by_date |
| L2 (synthesis, cited) | tunnel_state, context_recovery, what_do_i_think, thinking_trajectory, what_was_i_thinking, unfinished_threads, cognitive_patterns |
| L3 (fusion, route-to-attention) | dormant_contexts, open_threads, switching_cost, alignment_check |
| utility | github_search, query_analytics, list_principles, get_principle, tunnel_history |

### 2. Enforce citation discipline at L2+

Every tool at layer L2 or L3 MUST return output where every non-structural claim carries `[conv_id · YYYY-MM-DD]` or `[summary_title · YYYY-MM-DD]`. A programmatic verification step validates that no L2+ output contains prose claims without citations.

### 3. Move `enhanced-extraction-v5.txt` into the skills package

The summarizer prompt, currently loaded from `../../../clawd/cogro/prompts/enhanced-extraction-v5.txt`, moves to `.claude/skills/_prompts/enhanced-extraction-v5.txt`, loaded via `importlib.resources`. This fixes the critical launch blocker where public installs fail on first `brain-mcp summarize` with `FileNotFoundError`.

## Consequences

### Positive

1. **Auditability becomes structural.** Every synthesis claim traces to source conversations. AI consumers cannot fabricate decisions that have no citation footprint.
2. **The AI-first README pattern extends to contracts.** Skills tell the AI not just *when* to call a tool but *what layer it operates on, what it must not do, what citations it must include*. This completes the "For AI Assistants" loop.
3. **Launch blocker dissolves.** The hardcoded cogro prompt path becomes an internal skill asset.
4. **Positioning differentiates.** "First SHELET-compliant MCP server" is a unique frame against mem0/Letta/Zep. None of them have layer-bounded skills or citation-enforced output.
5. **Multi-tenant path opens.** The L0-L3 manifest pattern mirrors viter-workspace's forthcoming Supabase migrations (Migration B+C drafts at `viter-workspace/code/supabase/migrations/_drafts/`). When applied to brain-mcp (see ADR-002 / Migration 003), the same RLS pattern makes brain-mcp tenant-scoped.
6. **arXiv paper becomes publishable.** brain-mcp becomes the open-source reference implementation of the SHELET protocol, giving the paper a citeable artifact.

### Negative / Trade-offs

1. **Startup cost.** 25 SKILL.md files need to be written. Not 25 days of work — ~2-3 hours with good templates — but non-zero.
2. **Surface area increase.** The `.claude/skills/` directory becomes a versioned contract. Changes to tool behavior now require manifest updates or explicit deprecation.
3. **Risk of manifest/implementation drift.** If a skill's Python implementation diverges from its SKILL.md declaration, the system is silently lying. Mitigation: a CI check (`make verify-skills`) that introspects the MCP tool signatures and compares to the declared input/output contracts.
4. **Citation enforcement breaks some graceful-degradation paths.** When `tunnel_state` falls back to raw-conversation keyword search (no summaries available), the output cannot cite summaries that don't exist. The fallback path needs explicit "no L2 data, citations are L0" handling.

### Neutral

1. **Existing MCP tool signatures stay compatible.** The Python function API doesn't change. Only the governance wrapper is added. Existing Claude / Cursor / Windsurf integrations continue to work without modification.
2. **No performance impact.** Manifest is read at server startup; enforcement is a cheap dictionary lookup per tool invocation.

## Implementation Plan (7-day sprint)

| Day | Deliverable |
|---|---|
| 1 | Create `.claude/skills/` directory. Draft 4 SKILL.md files (brain-stats, trust-dashboard, semantic-search, search-conversations). This ADR. |
| 2 | Draft remaining 11 L1+L2 SKILL.md files. Move `enhanced-extraction-v5.txt` into `_prompts/`. Update `summarize.py:51` to load via `importlib.resources`. |
| 3 | Draft 4 L3 SKILL.md files. Implement citation enforcement in `tools_prosthetic.py` — every `[domain]` claim gains `[conv_id · date]` trail. |
| 4 | Draft remaining utility SKILL.md files. Write `make verify-skills` CI check. |
| 5 | Ship Migration 003 (`supabase/migrations/003_shelet_l0_to_l3.sql`) — the canonical Supabase tables mirroring viter's Migration B. Optional layer, off by default. |
| 6 | Update LAUNCH-POSTS.md r/mcp variant with SHELET framing. Update README "For AI Assistants" section to reference the skill manifests. |
| 7 | Tag v0.4.0. Sync CHANGELOG. Fire launch across r/ClaudeAI, r/mcp, r/adhdprogramming, HN. |

## References

- SHELET protocol source: `/Users/mordechai/clawd/poly-wiki-public/01-frameworks/bottleneck/shelet-protocol.md`
- SHELET evidence doc: `/Users/mordechai/clawd/poly-wiki-public/02-evidence/shelet-in-the-wild.md`
- Viter skills (pattern source): `/Users/mordechai/viter-workspace/.claude/skills/`
- Migration B draft (data-layer template): `/Users/mordechai/viter-workspace/code/supabase/migrations/_drafts/20260422000000_l0_to_l3_unified_data_model.sql.draft`
- Migration C draft (RLS template): `/Users/mordechai/viter-workspace/code/supabase/migrations/_drafts/20260423000000_enforce_tenant_rls.sql.draft`
- Intellectual-DNA synthesis: `/Users/mordechai/clawd/cogro/data/distilled/intellectual-dna-synthesis-2026-04-20.md`
- Governing rule (2026-04-19): "L0 is immutable, each layer is a pure function of the one below, every higher-layer claim carries a citation to the layer below."

## Related ADRs

- ADR-002 (future): Supabase canonical state + LanceDB/DuckDB operational projection
- ADR-003 (future): Citation enforcement mechanism — compile-time vs. runtime
- ADR-004 (future): Multi-tenant brain-mcp deployment model
