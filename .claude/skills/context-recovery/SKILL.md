---
name: context-recovery
description: Full re-entry brief for a domain — recent summaries + accumulated open questions, decisions, insights, quotes. Longer and deeper than tunnel-state. Use after days/weeks away from a project.
layer: L2
reads: [L2.summaries, L1.embeddings]
writes: []
citations: required
determinism: temporal
allowed-tools: mcp__my-brain__context_recovery
---

# context-recovery — L2 full re-entry brief

## Framework context

L2 synthesis with broader scope than `tunnel-state`. Fetches `summary_count + 10` latest summaries, presents the top N verbatim with importance icons (💎 breakthrough / ⭐ significant / · routine), then aggregates decisions, insights, and quotes across them. Every quoted item carries a citation.

Use when the user has been away from a domain long enough that tunnel-state alone is insufficient — they need to re-read their own recent thinking.

## When to invoke

- User says "refresh me on the X project" / "I've been away from Y for weeks"
- After dormant-contexts surfaces an abandoned domain and user wants to re-enter
- Paired with alignment-check when user is about to make a decision in a stale domain

## Input

```
domain: str
summary_count: int = 5
```

## Output contract

Markdown with:

```
## 🔄 Context Recovery: {domain}
**Stage**: ... · **Tone**: ... · **Conversations**: N

### 📋 Recent Summaries
{icon} **{title}** [{source} · {msg_count} msgs · date]
> {summary truncated to 300ch}

### Open questions (accumulated across all summaries)
- {q} [conv_id · date]

### Key decisions
- {d} [conv_id · date]

### Key insights
- {i} [conv_id · date]

### One quotable
> "{quote}" [conv_id · date]
```

## Does NOT do

- Re-synthesize the summaries (quote them verbatim)
- Invent a single "re-entry narrative" — the user assembles narrative from structured facts
- Hide stale data — if last summary is >90 days old, output prepends `⚠️ last activity N days ago`

## Fallback

No summaries → semantic search (embedding of domain, `min_sim=0.3`) + keyword expansion. Output tags each match with role icon (👤 user / 🤖 assistant). Footer declares L0-only operation.

## Verification checklist

- [ ] Every accumulated bullet has a citation
- [ ] Summaries are truncated, not paraphrased
- [ ] Recency warning present if latest summary is >90 days old
- [ ] Importance icons match declared values
