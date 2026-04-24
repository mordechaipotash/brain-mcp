---
name: open-threads
description: Global unfinished-work matrix — every domain × every open question. Paired with dormant-contexts for the complete overwhelm-reducer surface.
layer: L3
reads: [L2.summaries]
writes: []
citations: required
determinism: pure-function
allowed-tools: mcp__my-brain__open_threads
---

# open-threads — L3 global question matrix

## Framework context

L3 fusion that answers "what's everything that's still open, organized by where it lives." Complementary to `dormant-contexts` (which filters by importance and ranks by abandonment) and `unfinished-threads` (which is single-summary granularity).

## When to invoke

- User feels overwhelmed and asks for the full picture
- Planning session — show the landscape before choosing
- When `dormant-contexts` surfaces abandonment and user wants to see full depth

## Input

```
limit_per_domain: int = 5
max_domains: int = 20
```

## Output contract

Domain-grouped markdown. Each question carries `[conv_id · date]` or `[summary_id · date]`. Breakthrough count shown per domain when present.

## Does NOT do

- Prioritize across domains (that's the user's call)
- Collapse duplicate questions across domains (same question in two domains = two rows)
- Fabricate domain names

## Verification checklist

- [ ] Every question carries a citation
- [ ] Domains ordered by breakthrough count DESC → question count DESC
- [ ] Per-domain question list deduped by lowercase content
- [ ] Fallback to user-message questions when summaries absent
