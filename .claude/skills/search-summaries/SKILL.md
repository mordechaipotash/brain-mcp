---
name: search-summaries
description: Hybrid retrieval (vector + FTS) over L2 structured summaries. Supports filter by domain, importance, thinking-stage, source, and extract mode. Use when user wants structured knowledge, not raw messages.
layer: L1
reads: [L2.summaries]
writes: []
citations: output-is-citation
determinism: pure-function
allowed-tools: mcp__my-brain__search_summaries
---

# search-summaries — L1 retrieval over L2 structures

## Framework context

L1 retrieval skill whose substrate is the L2 summary table. Each result IS a citation back to the underlying conversation (via `conversation_id`) and to the summary row (for downstream L3 consumers). The "extract mode" parameter projects different structured fields from the summary payload — still no synthesis, just projection.

## When to invoke

- User wants decisions, open-questions, concepts, or quotables — not prose conversations
- After `semantic-search` finds candidate messages and you want the structured version
- Filtered queries: specific domain + importance + stage combination
- `extract="questions"` → open questions only. `extract="decisions"` → decisions only. `extract="quotes"` → quotables only. Default `extract="summary"`.

## Input

```
query: str
extract: "summary" | "questions" | "decisions" | "quotes" = "summary"
limit: int = 10
domain: str | None
importance: "breakthrough" | "significant" | "routine" | None
thinking_stage: "exploring" | "crystallizing" | "refining" | "executing" | None
source: str | None
mode: "hybrid" | "fts" | "vector" = "hybrid"
```

## Output contract

Markdown with per-row citations. Every row carries `[conv_id · date]` and `[summary_id]`. Extract modes project different structured fields but never paraphrase.

## Does NOT do

- Synthesize across summaries (that's L2 `what-do-i-think`)
- Fabricate filter values — invalid enum values return empty with explicit error
- Bypass sanitization (WHERE-clause allowlist `[a-zA-Z0-9\s\-_.,]` is enforced)
- Combine extract modes — one projection per call

## Verification checklist

- [ ] Every row has conv_id + summary_id citation
- [ ] Filter values pass sanitizer before SQL
- [ ] Mode fallback is explicit if LanceDB unavailable
- [ ] Re-run with identical args → identical order
