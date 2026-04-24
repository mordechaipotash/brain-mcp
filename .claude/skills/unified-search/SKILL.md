---
name: unified-search
description: Cross-source retrieval combining conversation embeddings, GitHub commits, and markdown docs into one ranked list. Use for broad orientation queries.
layer: L1
reads: [L1.embeddings, L0.github_commits, L0.markdown_docs]
writes: []
citations: output-is-citation
determinism: pure-function
allowed-tools: mcp__my-brain__unified_search
---

# unified-search — L1 cross-source retrieval

## Framework context

L1 retrieval that aggregates three substrate stores into one ranked list. No synthesis — just merge + sort. Fixed source-quotas (conversations: 5, github: 3, markdown: 3). Static score weights per source (0.4 github, 0.45 markdown) — conversation rows carry real cosine similarity.

## When to invoke

- User asks an orientation question that could be answered by conversations OR code OR notes
- First-pass before committing to a specific retrieval mode
- When the user says "anywhere I mentioned X"

## Input

```
query: str
limit: int = 15
```

## Output contract

Grouped markdown by source type. Each row has source tag `[conversation]` | `[github]` | `[markdown]` + citation (conv_id / commit SHA / file path).

## Does NOT do

- Rerank across sources with a cross-encoder (deprecated in fastembed migration)
- Dedupe semantically across sources — a GitHub commit and a conversation about that commit may both appear
- Hide missing substrates — if GitHub table absent, just skip GitHub rows; do not crash

## Verification checklist

- [ ] Results carry source tag + citation
- [ ] Missing substrates are silent (but logged to stderr)
- [ ] Sort order stable for identical query
