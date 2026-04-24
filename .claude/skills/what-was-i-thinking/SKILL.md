---
name: what-was-i-thinking
description: Monthly snapshot — activity level, top conversations, sample questions. Use when user wants to reconstruct a specific month's mental state.
layer: L2
reads: [L0.all_conversations]
writes: []
citations: required
determinism: pure-function
allowed-tools: mcp__my-brain__what_was_i_thinking
---

# what-was-i-thinking — L2 monthly snapshot

## Framework context

L2 skill bounded to a single YYYY-MM window. Computes monthly averages, classifies activity as HIGH (>1.5× monthly avg) / NORMAL / LOW (<0.5×). Unlike tunnel-state, this is a *temporal* window not a *topical* window.

## When to invoke

- User cites a specific month: "what was I doing in 2025-10"
- Reviewing a past hyperfocus period
- Before what-do-i-think, to orient temporally

## Input

```
month: str   # YYYY-MM format, strict
```

## Output contract

```
## 📅 What you were thinking: {month}
**Activity level**: 🔥 HIGH / 📊 NORMAL / 📉 LOW (N msgs vs avg M)
**Conversations**: N · **User messages**: M · **Questions asked**: K

### Top conversations (by message count)
1. **{title}** ({msg_count} messages) [conv_id · date]
...

### Sample questions that month
- "{q truncated 150ch}" [conv_id · date]
...
```

## Does NOT do

- Accept relative dates ("last month") — caller resolves to YYYY-MM
- Synthesize what the month "meant" — only structural stats + verbatim samples
- Compare months (that's trajectory)

## Verification checklist

- [ ] Month format strictly YYYY-MM (return error on malformed input)
- [ ] Activity level uses the 1.5× / 0.5× thresholds documented
- [ ] Top conversations cite conv_id
- [ ] Sample questions are verbatim (truncated 150ch)
