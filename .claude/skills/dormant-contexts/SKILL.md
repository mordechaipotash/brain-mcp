---
name: dormant-contexts
description: Surface abandoned domains with unresolved breakthrough/significant open questions. Ranked by breakthrough count → question count → conversation count. Use when user feels important threads have been dropped.
layer: L3
reads: [L2.summaries]
writes: []
citations: required
determinism: pure-function
allowed-tools: mcp__my-brain__dormant_contexts
---

# dormant-contexts — L3 abandoned-thread surface

## Framework context

L3 fusion skill. Aggregates across all domains, filters by importance threshold, ranks by breakthrough density. The alarm-bell for the prosthetic thesis: "what got left behind while you were tunnel-visioned elsewhere?"

## When to invoke

- User asks "what have I been neglecting" / "what did I drop"
- Weekly review ritual (paired with open-threads + unfinished-threads)
- After a long focused sprint — before committing to the next sprint

## Input

```
min_importance: "breakthrough" | "significant" = "significant"
limit: int = 20
```

## Output contract

Markdown list grouped by domain. Each domain row cites at least one summary_id + date for the most important abandoned question. Breakthrough markers (💎×N) for domains with multiple breakthroughs.

## Does NOT do

- Recommend which to resume (user decides)
- Auto-archive domains
- Dedupe across domains (domain is the aggregation unit)

## Verification checklist

- [ ] Each domain row cites at least one summary
- [ ] Filter enforces 3 conditions on open_questions (non-null, non-empty, not "none identified")
- [ ] Ranking: breakthrough count DESC → question count DESC → conv count DESC
- [ ] Fallback to raw conversations (grouped by title, ordered by last_active ASC) when summaries absent
