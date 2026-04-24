---
name: query-analytics
description: Query optional analytics parquets — timeline, stacks, problems, spend, summary. Use only when user asks about tool-stack adoption, spend, or temporal patterns.
layer: utility
reads: [L0.analytics_parquets]
writes: []
citations: output-is-citation
determinism: pure-function
allowed-tools: mcp__my-brain__query_analytics
---

# query-analytics — utility analytics surface

## Framework context

Utility skill over optional interpretation parquets (`data/facts/`, `data/interpretations/`). Softly degrades to "not available" if the parquets are absent — do NOT substitute the main conversation corpus.

## When to invoke

- User asks about tool-stack shifts over time
- Spend analysis ("how much did I spend on API calls in March")
- Temporal patterns (weekend vs weekday, monthly rhythms)

## Input

```
view: "timeline" | "stacks" | "problems" | "spend" | "summary" = "timeline"
date: str | None
month: str | None (YYYY-MM)
source: str | None
limit: int = 15
```

## Output contract

View-specific markdown. Always shows source parquet path (citation to the data file).

## Does NOT do

- Fabricate analytics if parquets absent (return "analytics parquets not found at {path}")
- Cross-reference with conversations (use unified-search for that)
- Aggregate beyond what the parquet provides

## Verification checklist

- [ ] Missing parquet → explicit "not found at {path}" response (do not fall back silently)
- [ ] View enum validated before query
- [ ] Output shows source parquet path (the citation)
- [ ] Ordering is deterministic per view (timeline=date DESC, spend=cost DESC, etc.)
