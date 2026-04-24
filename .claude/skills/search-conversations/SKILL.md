---
name: search-conversations
description: Deterministic keyword retrieval (ILIKE) over raw L0 messages. Use when the user references an exact term, name, or phrase they remember writing.
layer: L1
reads: [L0.all_conversations]
writes: []
citations: output-is-citation
determinism: pure-function
allowed-tools: mcp__my-brain__search_conversations
---

# search-conversations — L1 keyword retrieval

## Framework context

L1 skill paired with `semantic-search`. Where semantic-search operates on L1 embeddings, search-conversations operates on L0 text directly via DuckDB `ILIKE`. Deterministic, idempotent, pure function of (query, role filter, corpus).

## When to invoke

- User cites an **exact term** they remember ("what did I say about Persofi", "when I mentioned Ramchal")
- Proper nouns — names, project codewords, file paths, function names
- When semantic search is likely to dilute the signal (e.g., a unique proper noun will ILIKE-match cleanly)
- Empty `term` + `role="user"` → returns recent user questions (special mode)

## Input

```
term: str = ""      # keyword to match via ILIKE (case-insensitive)
limit: int = 15
role: str | None    # optional filter: "user" | "assistant"
```

## Output contract

Markdown table with:

```
| Date       | Source      | Role  | conv_id | Preview (200ch) |
|------------|-------------|-------|---------|-----------------|
```

Ordered by `created DESC` (newest first). Each row IS a citation.

## Does NOT do

- Fuzzy matching (that's `semantic-search`'s job)
- Synthesize preview content (200ch substring only, no paraphrase)
- Merge duplicate conversation IDs (consumers do that at L2)
- Escape `%` or `_` in the query (user's literal term is passed through ILIKE pattern with leading+trailing `%`)

## Execution

Internally wraps `brain_mcp.server.tools_conversations.search_conversations(term, limit, role)`. SQL: `WHERE content ILIKE ? [AND role = ?] ORDER BY created DESC LIMIT ?`.

## Verification checklist

- [ ] Every row has conv_id (L2 consumers need it for citations)
- [ ] Content preview is the first 200 chars of the matched message (via `substr(content, 1, 200)`), not a summary
- [ ] Empty-term + role="user" returns user questions (via `has_question = 1`)
- [ ] Re-running with identical args returns identical rows in identical order
- [ ] If term has no match, output is `f"No conversations found containing '{term}'"` — not an empty table
