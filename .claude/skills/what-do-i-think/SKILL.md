---
name: what-do-i-think
description: Synthesize the user's views on a topic across 10+ conversations. Modes synthesize (default) or precedent. Use when user is forming an opinion and may have already concluded something.
layer: L2
reads: [L2.summaries, L1.embeddings]
writes: []
citations: required
determinism: temporal
allowed-tools: mcp__my-brain__what_do_i_think
---

# what-do-i-think — L2 opinion synthesis

## Framework context

L2 synthesis that surfaces what the user has already decided/concluded about a topic before they re-ask. Deduplicates by 80-char lowercase prefix. Orders by importance (breakthrough > significant > routine). Every decision/question/quote carries a citation.

**Precedent mode** is a narrower projection: returns the closest past matches for a situation, filtered to exclude "none identified" decisions.

## When to invoke

- User says "what do I think about X" / "have I decided on Y"
- User is about to make a decision — surface their own prior thinking first
- Mode `precedent` when user cites a specific situation ("I'm facing a decision like Z")

## Input

```
topic: str
mode: "synthesize" | "precedent" = "synthesize"
```

## Output contract

**Synthesize mode:**
```
## What you think about: {topic}

### Top 5 summaries
{icon} **{title}** [conv_id · date]
> {summary_300ch}

### Key decisions (dedup by 80ch prefix, top 10)
- {decision} [conv_id · date]

### Still open (top 8)
- {question} [conv_id · date]

### Authentic quotes (top 5)
> "{quote}" [conv_id · date]
```

**Precedent mode:** top 10 closest matches, each row cites one summary with decision field.

## Does NOT do

- Declare "you think X" as a synthesized claim without a citation trail
- Merge contradictory views silently — surface both
- Paraphrase the decisions (verbatim, truncated to 200ch)

## Fallback

No summaries → Lance + keyword search on L0. Appends: *"Running without summaries. For richer synthesis: `brain-mcp summarize`"*

## Verification checklist

- [ ] Every decision/question/quote carries a citation
- [ ] Dedup logic is prefix-based (80ch lowercase)
- [ ] Top-5 summaries ordered by importance → stage → recency
- [ ] Empty fields are shown as "(none recorded)", not omitted
