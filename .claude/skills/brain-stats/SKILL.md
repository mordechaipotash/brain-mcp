---
name: brain-stats
description: Raw accounting of what's in the brain. Counts messages, embeddings, summaries, domains. Use when user asks "how much is in my brain" or "what's the size of the corpus."
layer: L0
reads: [L0.all_conversations, L1.embeddings, L2.summaries]
writes: []
citations: not-required
determinism: pure-function
allowed-tools: mcp__my-brain__brain_stats
---

# brain-stats — L0 accounting layer

## Framework context

This is an **L0 skill** in brain-mcp's SHELET stratification. L0 operations report on raw state without interpretation. Outputs are structural facts (counts, dates, sources) — not synthesized claims. No citations required because the numbers *are* the citation.

## When to invoke

- User asks "how much is in my brain" / "how many conversations" / "what's the corpus size"
- Onboarding — show what was successfully ingested
- Before any L2/L3 synthesis call, to decide if there's enough data to synthesize

## Input

```
view: "overview" | "domains" | "pulse" | "conversations" | "embeddings" | "github" | "markdown"
```

Default: `overview`.

## Output contract

Markdown report with quantitative facts only. Every number is exact (no "approximately"). Structure:

- Total message count
- Total conversation count
- Embedding count (if L1 pipeline run)
- Summary count (if L2 pipeline run)
- Per-source breakdown
- Date range (min/max msg_timestamp)
- Top 10 domains (for `domains` view)
- Thinking-stage matrix (for `pulse` view)

## Does NOT do

- Interpret what the numbers mean
- Recommend actions based on numbers
- Hide zero-count layers (if no embeddings, say "0" — don't skip the row)
- Fabricate counts if a data layer is missing (return "unavailable")

## Execution

Internally wraps `brain_mcp.server.tools_stats.brain_stats(view)`. The MCP tool is the implementation; this skill is the governance contract.

## Verification checklist

- [ ] Every count is an exact integer (no commas in the internal value, only in display)
- [ ] Missing layers report "unavailable" rather than crashing
- [ ] No prose claims beyond structural facts
- [ ] Output is idempotent — same corpus → same numbers
