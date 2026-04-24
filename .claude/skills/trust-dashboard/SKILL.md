---
name: trust-dashboard
description: Proof the safety net is intact. Structural integrity check across L0-L2. Use when user is anxious about what was lost or wants to verify nothing slipped.
layer: L0
reads: [L0.all_conversations, L1.embeddings, L2.summaries]
writes: []
citations: not-required
determinism: pure-function
allowed-tools: mcp__my-brain__trust_dashboard
---

# trust-dashboard — the safety net proof

## Framework context

L0 skill with a specific *psychological* purpose: reduce Zeigarnik-effect anxiety from unresolved threads. The prosthetic thesis says "the idle state IS the product" — the brain is working by *existing as a trusted safety net*. This skill is the evidence that the net works.

## When to invoke

- User expresses anxiety that something was lost
- User says "am I missing anything important"
- First-time setup — prove ingestion succeeded before they invest trust
- After a long break (>7 days) — reassure that the corpus is intact and resumable

## Output contract

Markdown report with:

- Total conversations (L0) + breakdown by source
- Total summaries (L2) + coverage % (summaries / conversations with ≥4 messages)
- Breakthrough count (importance = "breakthrough")
- Open question count (non-empty open_questions JSON across summaries)
- Decision count
- Most recent summary timestamp
- "Safety net status: ✅ intact" / "⚠️ N% unsummarized" / "❌ pipeline stale"

## Does NOT do

- Surface the content of any specific thread (that's L2 prosthetic tools)
- Analyze patterns (that's cognitive_patterns)
- Recommend action (that's L3 alignment_check)
- Hide gaps — always report the uncovered percentage honestly

## Execution

Internally wraps `brain_mcp.server.tools_stats.trust_dashboard()`.

## Verification checklist

- [ ] Coverage % is computed (not hardcoded)
- [ ] Zero-count items are reported as "0", not omitted
- [ ] "Safety net status" line is the first summary line
- [ ] If L2 pipeline never ran, output says "run `brain-mcp summarize` to enable structural checks"
