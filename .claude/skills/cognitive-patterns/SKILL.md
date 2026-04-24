---
name: cognitive-patterns
description: Surface patterns in how the user thinks — cognitive pattern, problem-solving approach, emotional tone, content category. Optional domain scope. Use when user asks "when do I think best" or "what patterns show up in breakthroughs."
layer: L2
reads: [L2.summaries]
writes: []
citations: required
determinism: pure-function
allowed-tools: mcp__my-brain__cognitive_patterns
---

# cognitive-patterns — L2 self-knowledge surface

## Framework context

L2 meta-skill. Aggregates pattern/approach/tone frequencies across summaries, separately counts *breakthrough-only* occurrences of each pattern. Synthesizes insight: "breakthroughs most associate with {top_pattern} and tend to happen when {top_tone}."

Unlike tunnel-state (what is the state), this is WHO is the thinker.

## When to invoke

- User asks "when do I do my best thinking"
- User asks "what happens right before breakthroughs"
- Self-reflection / coaching context
- Before alignment-check, when user wants to know if the decision style matches past breakthroughs

## Input

```
domain: str | None   # if None, aggregates across whole corpus
```

## Output contract

```
## 🧬 Cognitive Patterns {domain or '(all domains)'}
**Conversations**: N · **Breakthroughs**: M

### Cognitive patterns (top 10)
- {pattern}: count (💎 N of them led to breakthroughs) [N citations available]
...

### Problem-solving approaches (top 10)
- {approach}: count (💎 N breakthroughs)
...

### Emotional tones (top 8)
- {tone}: count (💎 N breakthroughs)

### Breakthrough insight
Breakthroughs most associate with **{top_pattern}** cognitive pattern and tend to happen when you're feeling **{top_tone}**.
```

## Does NOT do

- Claim causation — correlation only
- Prescribe a pattern as "better"
- Include raw conversation text (this is a meta-view)

## Fallback

No summaries → basic activity stats (msgs/questions/sources/months), with footer *"Cannot determine cognitive patterns without summaries — showing activity stats"*.

## Verification checklist

- [ ] Breakthrough counts are separate from total counts
- [ ] Each pattern row notes "citations available" — caller can drill down via search-summaries
- [ ] Insight is a synthesis of top_pattern + top_tone (deterministic, not LLM-generated)
- [ ] Empty DB → explicit "no data found"
