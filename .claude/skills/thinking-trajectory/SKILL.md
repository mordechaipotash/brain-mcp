---
name: thinking-trajectory
description: Show how the user's thinking on a topic evolved over time. Views full (timeline + stages), velocity (accelerating/stable/declining), first (genesis moment). Use when the user might have already moved past where they seem to be now.
layer: L2
reads: [L1.embeddings, L2.summaries, L0.all_conversations]
writes: []
citations: required
determinism: temporal
allowed-tools: mcp__my-brain__thinking_trajectory
---

# thinking-trajectory — L2 evolution synthesis

## Framework context

L2 skill answering "how has my thinking on X changed over time." Three views projecting different slices:

- **full**: semantic + keyword temporal distribution + thinking-stage progression
- **velocity**: ACCELERATING (recent > 1.5× early) / DECLINING (recent < 0.5× early) / STABLE / INSUFFICIENT (<6 periods)
- **first**: genesis moment + span in days + first 300ch of the first mention

## When to invoke

- User seems stuck repeating a question they've already explored
- User asks "how did I get here" / "when did I first think about X"
- Before a decision, to see if the user's own thinking has accelerated, stalled, or reversed
- Verify claim of "I just started thinking about X" (often untrue in this corpus)

## Input

```
topic: str
view: "full" | "velocity" | "first" = "full"
```

## Output contract

**Full view:**
```
## 📈 Thinking Trajectory: {topic}

### Timeline (by month)
2025-08  ███████ 42 msgs  [top_conv_id · date]
2025-09  ███ 18 msgs   [top_conv_id · date]
...

### Thinking stage progression
🔍 exploring (2025-08 to 2025-10) → 💎 crystallizing (2025-11) → 🔧 refining (2026-02) → 🚀 executing (2026-03+)

### First mention
[conv_id · 2025-08-14] > "{300ch preview}"
```

**Velocity view:**
```
## ⚡ Trajectory Velocity: {topic}
📈 ACCELERATING (recent 3 months avg: X, early 3 months avg: Y)
or 📉 DECLINING or ➡️ STABLE or 📊 INSUFFICIENT DATA (<6 periods)
```

**First view:** single row — genesis_timestamp, first_title, first_300ch_preview, total span in days, total mention count.

## Does NOT do

- Interpret trajectory direction as good/bad (user interprets)
- Predict future mention count
- Conflate "mention" with "understanding" — a topic can peak in mentions while thinking stage remains exploring

## Verification checklist

- [ ] Velocity formula thresholds are 1.5× / 0.5× (hard thresholds, not tunable in this version)
- [ ] Stage progression is ordered by stage_order dict: exploring=0 → crystallizing=1 → refining=2 → executing=3
- [ ] First-mention has exact timestamp + title + citation
- [ ] <6 periods → explicit INSUFFICIENT DATA, not silent stable
