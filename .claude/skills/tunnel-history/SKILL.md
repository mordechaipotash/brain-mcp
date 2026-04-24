---
name: tunnel-history
description: Show aggregated history of a domain — thinking stages, importance breakdown, cognitive patterns, emotional tones, top concepts as bar charts. Use when user wants the meta-view of their own engagement with a topic over time.
layer: utility
reads: [L2.summaries, L0.all_conversations]
writes: []
citations: output-is-citation
determinism: pure-function
allowed-tools: mcp__my-brain__tunnel_history
---

# tunnel-history — utility meta-view

## Framework context

Utility skill adjacent to tunnel-state: where tunnel-state is the current save-state, tunnel-history is the aggregate engagement profile across all time. Bar charts for distributions, not citations-per-row — the output IS the distribution.

## When to invoke

- User wants a retrospective: "overall, how have I been with X"
- Before thinking-trajectory to establish baseline distribution
- Coaching / self-reflection context

## Input

```
domain: str
```

## Output contract

```
## 📊 Tunnel History: {domain}
**Total conversations**: N
**Importance**: 💎 X · ⭐ Y · · Z

### Thinking stages (bar chart)
🚀 executing      ████████████  60%
🔧 refining       ████          20%
💎 crystallizing  ██            10%
🔍 exploring      ██            10%

### Cognitive patterns (top 7)
- deep-dive: 45
- architectural: 32
...

### Problem-solving approaches (top 7)
### Emotional tones (top 5)
### Top concepts (top 10 by frequency)
```

## Does NOT do

- Cite individual conversations (this is aggregate)
- Show evolution over time (that's thinking-trajectory)
- Compare domains (tunnel-history is single-domain)

## Verification checklist

- [ ] Bar chart uses `█` character with 5% per char
- [ ] Percentages sum to 100 (±1 for rounding)
- [ ] Top-N slices are deterministic sort (count DESC, name ASC tiebreak)
