---
name: get-principle
description: Return the full body of one principle by name. Use when user references a specific principle (e.g., "SHELET", "Bottleneck Amplification").
layer: utility
reads: [principles.yaml]
writes: []
citations: not-required
determinism: pure-function
allowed-tools: mcp__my-brain__get_principle
---

# get-principle — utility principle lookup

## Framework context

Utility single-row lookup over the principles YAML. Lowercase exact-match on name, then fuzzy-match on contains.

## When to invoke

- User cites a principle by name and wants the full definition
- Paired with alignment-check output when user wants to drill into a matched principle

## Input

```
name: str   # principle name (case-insensitive)
```

## Output contract

Markdown: name, definition, core_formula, implementation_formula (if present), description (if present).

## Does NOT do

- Fabricate if not found (return "principle '{name}' not found, try list-principles")
- Return multiple matches (one principle per call)

## Verification checklist

- [ ] Name match is case-insensitive exact first, then contains
- [ ] Not-found response suggests `list-principles`
- [ ] If YAML file absent, explicit "no principles file configured"
- [ ] Return full body — do not truncate principle text
