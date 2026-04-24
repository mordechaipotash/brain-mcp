---
name: github-search
description: Query the user's GitHub repo + commit index. Modes timeline, conversations, code, validate. Use when user references a repo, commit, or wants to cross-reference code with conversations.
layer: utility
reads: [L0.github_repos, L0.github_commits, L0.all_conversations, L1.embeddings]
writes: []
citations: output-is-citation
determinism: pure-function
allowed-tools: mcp__my-brain__github_search
---

# github-search — utility cross-reference

## Framework context

Utility skill that bridges conversation corpus ↔ code corpus. Four modes project different joins. Every result IS a citation (repo + commit SHA, or conv_id).

## When to invoke

- User references a specific repo by name
- User asks "when did I first commit X" / "what was I building around commit Y"
- Validate a claim: did the user really work on X before they said they did? (`mode=validate`)
- Cross-reference: conversations that mention repo + commits that match

## Input

```
query: str = ""
project: str | None
mode: "timeline" | "conversations" | "code" | "validate" = "timeline"
limit: int = 10
```

## Output contract

Mode-specific markdown. Always includes: repo name, commit timestamp (when applicable), conv_id (when applicable).

## Does NOT do

- Fetch code from GitHub live (works off the indexed parquet)
- Cross-repo search in a single query (one repo at a time)
- Claim a predates-project warning without commit evidence

## Verification checklist

- [ ] `validate` mode surfaces the predates-project warning when a conversation predates the earliest commit
- [ ] `timeline` mode shows repo created_at + commits DESC
- [ ] `code` mode uses semantic search across commit messages
