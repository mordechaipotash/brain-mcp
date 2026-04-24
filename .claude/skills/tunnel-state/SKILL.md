---
name: tunnel-state
description: Reconstruct cognitive save-state for a domain. Returns thinking-stage, open-questions, decisions, concepts, emotional-tone — every claim carries a citation. Use when user returns to a topic.
layer: L2
reads: [L2.summaries]
writes: []
citations: required
determinism: temporal
allowed-tools: mcp__my-brain__tunnel_state
---

# tunnel-state — L2 domain save-state synthesis

## Framework context

**Flagship L2 prosthetic skill.** Synthesizes across the latest N summaries for a domain into a unified "save state." Every claim MUST carry `[conv_id · YYYY-MM-DD]` or `[summary_id · date]` citation — this is not decorative, it is the structural contract that distinguishes L2 from L1.

Purpose rooted in Masicampo & Baumeister: *having a plan* for an unfulfilled goal eliminates intrusive cognitive nagging, even without executing it. The output is the plan.

## When to invoke

- User says "where did I leave off with X" / "what was I doing on the Y project"
- User is about to commit to a domain switch (pair with switching-cost)
- After a break of 3+ days from a domain
- NEVER invoke for "what do I think about X" (that's `what-do-i-think`)

## Input

```
domain: str
limit: int = 10   # how many latest summaries to synthesize across
```

## Output contract

Markdown with structured sections, every bullet carrying a citation:

```
## 🧠 Tunnel State: {domain}
**Stage**: executing | refining | crystallizing | exploring
**Emotional tone**: ... [last_summary_id · date]
**Conversations summarized**: N · **Breakthroughs**: M

### Open questions
- {question} [conv_id · date]
...

### Recent decisions
- {decision} [conv_id · date]
...

### Active concepts
{concept}, {concept}, ... (flat list — concepts do not cite individually)

### Key insights
- {insight} [conv_id · date]

### Connected domains
{domain}, {domain}, ... [no individual citation]
```

## Does NOT do

- Cross-domain synthesis (that's L3 `switching-cost` / `alignment-check`)
- Recommend what to do next (that's L3 `open-threads`)
- Fabricate stage/tone when no summaries exist — use fallback path with `_SUMMARIES_HINT` footer
- Return a single-line summary — the output IS the structure, by design

## Fallback (no summaries)

If L2 summaries are missing, degrade to keyword-expand search on L0 via `_DOMAIN_KEYWORDS`. Output explicitly says "operating from L0 only, citations are conversation-level" and appends `_SUMMARIES_HINT`.

## Verification checklist

- [ ] Every bullet has a citation — no uncited claims allowed
- [ ] Stage enum is from the valid set (no freeform strings)
- [ ] If domain unknown, return "no data for domain X" (do not approximate)
- [ ] Re-run within same summaries set → identical output (temporal = depends only on inputs frozen at call time)
