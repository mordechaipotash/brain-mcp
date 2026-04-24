---
name: get-conversation
description: Fetch the full message thread of a specific conversation by ID. Returns role + content + timestamp, truncated at 20 messages / 1000 chars per message. Use when the user references a specific conversation from prior search results.
layer: L0
reads: [L0.all_conversations]
writes: []
citations: output-is-citation
determinism: pure-function
allowed-tools: mcp__my-brain__get_conversation
---

# get-conversation — L0 raw thread retrieval

## Framework context

L0 skill for direct thread access. The conversation ID IS the citation. Returns raw messages without synthesis or filtering.

## When to invoke

- User references a conv_id from a previous search result ("show me that conversation about X")
- Drilling from citation back to source (every L2/L3 citation chain terminates here)
- Debugging: verify what a summary is derived from

## Input

```
conversation_id: str
```

## Output contract

```
## Conversation: {conversation_title}
**Source**: {source} · **Created**: {date} · **Messages**: N

👤 [timestamp] {first 1000ch of user message}
🤖 [timestamp] {first 1000ch of assistant reply}
...

_Showing first 20 of N messages_
```

## Does NOT do

- Synthesize or summarize (that's L2 `tunnel-state` / `context-recovery`)
- Render more than 20 messages (caller must iterate if needed)
- Render more than 1000 chars per message
- Follow references to other conversations

## Verification checklist

- [ ] conv_id unknown → explicit "Conversation not found: {id}"
- [ ] Messages ordered by msg_index ASC
- [ ] Truncation notice present when > 20 messages
- [ ] Timestamps in ISO format (not localized)
