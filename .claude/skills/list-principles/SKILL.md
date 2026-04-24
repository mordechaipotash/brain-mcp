---
name: list-principles
description: List the user's stated principles from the configured YAML file. Use when user asks "what are my principles" or before alignment-check.
layer: utility
reads: [principles.yaml]
writes: []
citations: not-required
determinism: pure-function
allowed-tools: mcp__my-brain__list_principles
---

# list-principles — utility principles surface

## Framework context

Utility skill that dumps the principles YAML contents. Read-only, pure function of the file.

## When to invoke

- User asks "what are my principles"
- Before alignment-check, to remind user of the active principle set
- When user wants to audit/edit their principle file

## Output contract

Markdown list. Each principle: name + one-line definition. If YAML has sections, preserve section headers.

## Does NOT do

- Modify the YAML
- Rank principles
- Interpret applicability — that's alignment-check

## Verification checklist

- [ ] If YAML file absent, return explicit "no principles file configured at {path}"
- [ ] Section headers preserved if present in YAML
- [ ] Ordering follows YAML file order (stable — do not re-sort)
