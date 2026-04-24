---
name: alignment-check
description: Evaluate a decision against the user's stated principles + related past thinking. Loads principles from YAML, matches by keyword, surfaces semantically similar prior decisions.
layer: L3
reads: [principles.yaml, L1.embeddings, L0.all_conversations]
writes: []
citations: required
determinism: temporal
allowed-tools: mcp__my-brain__alignment_check
---

# alignment-check — L3 principle/decision fusion

## Framework context

L3 fusion skill that joins two different substrates: the user's stated principles (YAML file) + their past thinking (L1 vector search). Produces an evaluation surface for a pending decision. The user interprets — this skill surfaces the relevant material.

## When to invoke

- User is about to make a decision and wants a principles gut-check
- Paired with switching-cost when the decision involves abandoning a domain
- Before any irreversible action (hiring, contract signing, technology stack commitment)

## Input

```
decision: str   # the decision under consideration, natural-language
```

## Output contract

```
## 🎯 Alignment Check: "{decision (truncated 80ch)}"

### Relevant principles (top 3)
- **{principle_name}**: {definition truncated 200ch}
  *core formula: {formula}*
- ...

### Related past thinking (top 5)
- [{date}] **{conversation_title}** [conv_id]
  > {300ch preview}

### Surface, do not decide
The user reconciles principles with past thinking. This skill does not return a verdict.
```

## Does NOT do

- Return a verdict (aligned / misaligned) — user decides
- Weigh principles against each other
- Fabricate principles if YAML is absent — return "no principles file configured"
- Cache results (temporal — same decision text may match different principles over time as the YAML evolves)

## Principles matching

- Dict form: iterates key/value pairs, matches by name+definition contains any word from decision (length > 4)
- List form: same matching on name+definition+description fields
- Max 3 principles surfaced, truncated to 200ch each

## Semantic search threshold

`min_sim=0.35` (stricter than tunnel-state's 0.3 — fewer but higher-quality past-thinking matches).

## Verification checklist

- [ ] Each principle shows name + definition + core_formula (if available)
- [ ] Each past-thinking item cites conv_id + date
- [ ] "Surface, do not decide" footer is always present
- [ ] If principles YAML absent, output is explicit about what's missing
