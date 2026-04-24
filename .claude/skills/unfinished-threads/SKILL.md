---
name: unfinished-threads
description: Conversations still in exploring or crystallizing stage with open questions at or above min_importance. Use when user feels there's unfinished work but doesn't know where.
layer: L2
reads: [L2.summaries]
writes: []
citations: required
determinism: pure-function
allowed-tools: mcp__my-brain__unfinished_threads
---

# unfinished-threads — L2 open-work surface

## Framework context

L2 skill that filters to summaries matching:
- `thinking_stage IN ('exploring', 'crystallizing')`
- `importance IN ('breakthrough', 'significant')` (or as configured)
- `open_questions` non-null and non-empty and not "none identified"

Orders by importance DESC, then msg_count DESC. Max 25 rows.

Distinct from L3 `open-threads` because this is single-summary granularity — one row per conversation — not domain-aggregated.

## When to invoke

- User says "what's still unfinished" or "what's open that matters"
- Weekly review ritual
- Before planning sessions — surface what you owe yourself

## Input

```
domain: str | None
importance: "breakthrough" | "significant" = "significant"
```

## Output contract

```
## 🧵 Unfinished Threads {domain or 'across all domains'}
**Importance filter**: {importance}+  · **Results**: N

💎 **{title}** · {domain_primary} · {stage}
[conv_id · date] · {msg_count} msgs
> {summary 200ch}

Open questions (max 3 shown):
- {q 200ch}
- {q 200ch}
```

## Does NOT do

- Aggregate across conversations (that's L3 `open-threads`)
- Close threads automatically
- Mark threads as abandoned (that's L3 `dormant-contexts`)

## Verification checklist

- [ ] Filter enforces the 3 conditions on open_questions (non-null, non-empty, not "none identified")
- [ ] Each row cites conv_id
- [ ] Open questions shown verbatim (max 3 per row)
- [ ] Ordering: importance DESC → msg_count DESC
