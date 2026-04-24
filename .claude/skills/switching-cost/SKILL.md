---
name: switching-cost
description: Quantify cognitive cost of switching from current domain to target domain. Formula-driven (0.0-1.0). Returns recommendation. Use BEFORE committing to a domain switch.
layer: L3
reads: [L2.summaries]
writes: []
citations: required
determinism: pure-function
allowed-tools: mcp__my-brain__switching_cost
---

# switching-cost — L3 attention-switch scorer

## Framework context

**Flagship L3 skill.** The only quantified attention-economics tool in the suite. Score is a deterministic function of current-domain open-question count, current-domain thinking stage, and concept overlap between domains.

**Formula** (copied from `tools_prosthetic.py:683-686`):
```
oq_cost         = min(len(cur_open_questions) / 10.0, 1.0)
overlap_discount = min(len(shared_concepts) / max(len(cur_concepts), 1), 1.0)
stage_cost      = {executing: 0.8, refining: 0.6, crystallizing: 0.4, exploring: 0.2}[cur_stage]
score           = (oq_cost * 0.35) + (stage_cost * 0.35) - (overlap_discount * 0.3)
```

Score thresholds: `<0.3` low / `0.3–0.6` moderate / `>0.6` high.

## When to invoke

- User says "should I switch to X" / "is it worth pausing Y for Z"
- Auto-invoke before tunnel-state(target) when user was just in a different tunnel-state
- Pair with context-recovery when score is high but switch is necessary

## Input

```
current_domain: str
target_domain: str
```

## Output contract

```
## 🔀 Switching Cost: {current} → {target}
**Score**: 0.XX (✅ Low / ⚠️ Moderate / 🔴 High)
**Recommendation**: "Low cost — go for it" | "Moderate — consider noting open questions first" | "High — significant unfinished work"

### Breakdown
- oq_cost: X.XX (N open questions in current)
- stage_cost: X.XX (current stage: {stage})
- overlap_discount: X.XX ({shared} shared concepts out of {total})

### Questions you'd leave behind (top 5)
- {question} [conv_id · date]

### Shared concept bridges
{concept}, {concept}, ...
```

## Does NOT do

- Recommend which domain is "better"
- Factor in deadlines or external constraints (user does that)
- Cache the score — always recomputed from latest summaries
- Extrapolate to 3+ domains (pairwise only)

## Fallback

Without summaries: heuristic based on (question count, message volume, shared conversation titles). Returns a score of same range but with different weights. Output explicitly labels `[heuristic fallback]`.

## Verification checklist

- [ ] Score clamped to [0.0, 1.0]
- [ ] Formula weights are 0.35/0.35/-0.30 (hardcoded — not tunable in this version)
- [ ] Recommendation string matches the three threshold bands
- [ ] Leave-behind questions cite source
- [ ] Score is deterministic — same inputs → same score
