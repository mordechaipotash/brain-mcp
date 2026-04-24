---
name: search-docs
description: Retrieval over the user's markdown corpus (notes, wikis, docs) with filters for depth-score, breakthroughs, project, and open TODOs. Use when user references a markdown file or wiki concept.
layer: L1
reads: [L0.markdown_docs]
writes: []
citations: output-is-citation
determinism: pure-function
allowed-tools: mcp__my-brain__search_docs
---

# search-docs — L1 retrieval over markdown corpus

## Framework context

L1 retrieval over the markdown_docs DuckDB table (optional L0 layer populated from user's markdown vault). Multiple filter sub-modes project different slices. Each result IS a citation (file path + line region).

## When to invoke

- User references a known doc: "in my notes on X"
- Cross-reference to conversations: unified-search uses this internally
- Mode `filter="breakthrough"` for high-energy docs
- Mode `filter="todos"` for open TODO extraction across projects
- Mode `filter="deep"` with `min_depth=70` for substantive docs only

## Input

```
query: str = ""
filter: None | "ip" | "breakthrough" | "deep" | "project" | "todos"
project: str | None
limit: int = 15
min_depth: int = 70
```

## Output contract

Markdown table with filename, project, voice, energy, depth_score, harvest_score, decision_count, word_count, first_line. Each row is a path-citation.

## Does NOT do

- Read full file contents (preview is 100-300 chars only)
- Interpret `energy` or `voice` tags
- Fall back silently when markdown table is absent — return "markdown corpus not found, run markdown ingester"

## Verification checklist

- [ ] Result ordering matches declared filter mode (default: depth_score DESC, harvest_score DESC)
- [ ] Filter enum is validated
- [ ] IP mode requires embeddings; falls back explicitly if not present
