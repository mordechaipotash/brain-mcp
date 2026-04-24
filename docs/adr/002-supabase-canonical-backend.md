# ADR-002: Supabase as Optional Canonical Backend for SHELET Layers

**Status**: Proposed (deferred — migration 003 ships; wiring deferred to follow-up)
**Date**: 2026-04-24
**Deciders**: Mordechai Potash
**Supersedes**: —
**Supersededby**: —
**Tags**: persistence, multi-tenant, supabase, shelet

---

## Context

ADR-001 introduced the SHELET L0-L3 stratification and implemented it via:
- Local DuckDB-over-Parquet for L0 (`all_conversations.parquet`)
- Local LanceDB for L1 (embeddings)
- Local parquet + LanceDB for L2 (`brain_summaries_v6.parquet`)
- In-process composition for L3 (tool outputs)

This works for single-user local deployment. But it cannot:

1. Support multi-tenant deployments (brain-mcp as a hosted product)
2. Enforce layer-bounded permissions at the storage layer (RLS)
3. Provide structural citation-chain resolution as a remote SQL call
4. Serve as the authoritative state for cross-machine sync

[Migration 003](../../supabase/migrations/003_shelet_l0_to_l3.sql) ships the Supabase schema that solves all four. But the Python-side adapter that would let brain-mcp *use* Supabase as a backend is not yet written.

## Decision

**Ship the migration, defer the adapter.** The schema is the commitment — if it exists in `supabase/migrations/`, the contract is stable and users can apply it manually to a Supabase project today. The Python adapter that writes to / reads from those tables ships in a follow-up milestone.

Concretely:

- **Shipped in v0.4.0** (ADR-001 / this ADR):
  - Migration 003 with full schema, RLS, indexes, and `brain.resolve_citations(l3_id)` recursive-chain resolver
  - Schema documentation in the migration file itself
  - This ADR as the commitment to the interface

- **Deferred to v0.5.0**:
  - `brain_mcp.supabase_adapter` module with L0/L1/L2/L3 writers
  - `brain-mcp setup --supabase` CLI flag
  - `[supabase]` section in `brain.yaml` config schema
  - Tenant management CLI (`brain-mcp tenants create / invite`)
  - Bidirectional sync between local (DuckDB + LanceDB) and Supabase canonical

## Consequences

### Positive

1. **Schema commitment is public.** Users applying Migration 003 to their own Supabase projects can build adapters themselves, and we can build against a stable contract.
2. **Launch velocity preserved.** ADR-001 + skill pack + citation discipline + prompt fix are enough for a meaningful v0.4.0 launch. Supabase wiring is not blocking.
3. **Migration 003 is forward-compatible.** The citations CHECK constraint (`jsonb_array_length(citations->'l2_ids') > 0` etc.) encodes the governance rule at the DB level. Any future adapter that violates it fails at INSERT, not at review.

### Negative

1. **Users who want multi-tenant today have to write their own adapter.** Acceptable for v0.4.0 (no one is asking for this yet); unacceptable long-term.
2. **Risk of adapter drift.** Six months of schema evolution in viter-workspace's Migration B without a brain-mcp adapter to keep honest could lead to divergence.
3. **Documentation debt.** The migration file needs clearer prose explaining which columns map to which existing `brain_summaries_v6` fields when someone does write the adapter.

### Neutral

1. **The skills already declare their `reads:` and `writes:` at layer granularity.** When the adapter ships, it implements the same contract with different substrate — no SKILL.md changes needed.

## Implementation Plan (v0.5.0 target)

| Phase | Deliverable | Owner |
|---|---|---|
| 1 | `brain_mcp/supabase_adapter.py` stub with `BrainSupabase` class: `.write_l0()`, `.write_l1()`, `.write_l2()`, `.write_l3()`, `.query()`, `.resolve_citations()` | Mordechai |
| 2 | `brain-mcp setup --supabase <project-url>` CLI flag — applies Migration 003, writes config | Mordechai |
| 3 | `[supabase]` config section in `brain.yaml`: project_url, anon_key, service_role_key (env-ref), tenant_id | Mordechai |
| 4 | Bidirectional sync: `brain-mcp sync --to-supabase` / `--from-supabase` | Mordechai |
| 5 | Multi-tenant membership CLI: `brain-mcp tenants {create,invite,list,leave}` | Mordechai |
| 6 | Remote-first deployment mode: tool calls go directly against Supabase via supa-brain parallel MCP server | Mordechai |

## References

- ADR-001 (SHELET reference implementation): [docs/adr/001-shelet-reference-implementation.md](001-shelet-reference-implementation.md)
- Migration 003 schema: [supabase/migrations/003_shelet_l0_to_l3.sql](../../supabase/migrations/003_shelet_l0_to_l3.sql)
- Viter Migration B (pattern source, still in drafts): `/Users/mordechai/viter-workspace/code/supabase/migrations/_drafts/20260422000000_l0_to_l3_unified_data_model.sql.draft`
- Viter Migration C (RLS pattern, still in drafts): `/Users/mordechai/viter-workspace/code/supabase/migrations/_drafts/20260423000000_enforce_tenant_rls.sql.draft`
- supa-brain MCP server (parallel Supabase-backed server): `~/projects/supa-brain/` (separate repo)
- Open design question: "Same L0 → multiple L1s" (Yitzchak observation, 2026-04-22) — Migration 003 supports this via `UNIQUE (l0_id, extraction_type, extractor_version)` but the adapter semantics need a write-deduplication strategy
