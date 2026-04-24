---
name: semantic-search
description: Deterministic vector retrieval over embedded messages. Finds conversations by meaning, not keyword. Use when the user's phrasing may not match what they wrote at the time.
layer: L1
reads: [L0.all_conversations, L1.embeddings]
writes: []
citations: output-is-citation
determinism: pure-function
allowed-tools: mcp__my-brain__semantic_search
---

# semantic-search — L1 vector retrieval

## Framework context

L1 skill: **pure function of L0 given a fixed embedding model**. Same query + same corpus + same model → identical results, always. No LLM judgment. The returned message IDs are themselves the citations — no synthesis is performed.

This is the retrieval layer that feeds all L2 synthesis skills. When `tunnel_state` or `what_do_i_think` is called with a topic, it calls `semantic-search` first.

## When to invoke

- User's phrasing is conceptual ("what do I think about X") rather than verbatim
- Starting any new topic — check if they've thought about this before
- Before any L2 synthesis to seed the candidate set
- When keyword search (`search-conversations`) returns zero results but the topic probably was discussed

## Input

```
query: str          # natural language query
limit: int = 10     # number of results to return
```

## Output contract

Markdown list of the top N results, each carrying **structural citations**:

```
### [N] {conversation_title}
**Similarity**: 0.XXXX
**Source**: {source} · **Date**: YYYY-MM-DD · **conv_id**: {id}

> {400-char preview}
```

Each result IS a citation. No synthesis. No interpretation. No merging.

## Does NOT do

- Summarize the results (that's L2 `what_do_i_think`)
- Deduplicate semantically similar results (that's L3 `dormant_contexts`)
- Filter by domain without explicit request (that's L2 `tunnel_state`)
- Rewrite the query for the user — if query is bad, empty result is honest

## Execution

Internally wraps `brain_mcp.server.tools_search.semantic_search(query, limit)`. Embedding generated via `get_embedding(f"search_query: {query}")`, searched against LanceDB `message` table, ranked by `1/(1+distance)`.

## Verification checklist

- [ ] Similarity score is present on every result
- [ ] conv_id is present on every result (future L2 will cite it)
- [ ] If embedding model unavailable, output explicit "embedding unavailable" message — do not silently fall back to keyword
- [ ] Result order is distance-sorted ascending (similarity descending)
- [ ] Re-running with identical query returns identical result order
