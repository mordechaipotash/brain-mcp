# brain-mcp Launch Posts — v0.3.1

## 1. r/ClaudeAI

**Title:** I built an MCP server that gives Claude memory across conversations — 25 tools, 100% local, 30-second setup

**Body:**

Every Claude conversation starts from zero. You explained your project architecture last week? Gone. That decision you made about your database schema? Claude has no idea.

I kept losing hours re-explaining context, so I built **brain-mcp** — an open-source MCP server that indexes your conversation history from Claude, Cursor, Windsurf, and Gemini CLI, then makes it all queryable.

It's not just search. The goal is **cognitive prosthetics** — tools that reconstruct your mental state:

- `tunnel_state("backend-refactor")` → returns where you left off, open questions, last decisions — like loading a save game
- `context_recovery("auth-system")` → full recovery brief when you're returning to something after days away
- `semantic_search` → find that conversation where you figured out the caching strategy

**The part I'm most excited about:** the README has a "For AI Assistants" section *at the top* that teaches Claude WHEN to search your history and HOW to present results. There's also a dedicated page at [brainmcp.dev/for-ai](https://brainmcp.dev/for-ai). The idea is that your AI shouldn't wait for you to ask — it should proactively pull in relevant context.

**Stats:** 25 MCP tools, ~12ms context recovery, 100% local (no cloud, no API keys), MIT licensed.

**Setup is one command:**

```
pipx install brain-mcp && brain-mcp setup
```

It auto-discovers your conversation sources, imports them, runs embeddings locally, and configures your MCP clients. Takes about 30 seconds.

Still actively building — the ChatGPT importer needs more work, and I have ideas for more prosthetic tools. But it's been genuinely useful for my own workflow already.

GitHub: https://github.com/mordechaipotash/brain-mcp
Site: https://brainmcp.dev

Happy to answer questions about the architecture or MCP integration.

---

## 2. r/mcp

**Title:** brain-mcp v0.4.0 — the first SHELET-compliant MCP server. 25 stratified skills, structural citation discipline, layer-bounded permissions.

**Body:**

Most MCP memory servers are a bag of tools over a shared data store. brain-mcp is built on a governance model I've been developing called **SHELET** (Stratified Human-Engaged Leverage Enhancement Technology). Every tool declares what layer it operates on, what it reads, what it writes, and what citations it must return.

**The four layers:**

- **L0** — raw, immutable conversation artifacts (INSERT-only)
- **L1** — deterministic extractions (same input → same output, pure function of L0: embeddings, keyword indexes)
- **L2** — LLM synthesis, temporal, **citations required** (every claim carries `[conv_id · date]`)
- **L3** — fusion / route-to-attention (dormant contexts, switching cost, alignment check)

**Governing rule (encoded at the schema level):**
> *L0 is immutable. Each layer is a pure function of the one below. Every higher-layer claim carries a citation to the layer below.*

This isn't convention — it's enforced. L2 rows have a `CHECK` constraint requiring non-empty citations JSONB. The RLS policies prevent an L3 tool from writing to L0. The `make verify-skills` CI check validates every SKILL.md manifest against the registered MCP tool surface.

**The 25 tools stratified:**

| Layer | Tools |
|---|---|
| L0 | brain-stats, trust-dashboard, get-conversation |
| L1 | semantic-search, search-conversations, search-summaries, search-docs, unified-search, conversations-by-date |
| L2 | tunnel-state, context-recovery, what-do-i-think, thinking-trajectory, what-was-i-thinking, unfinished-threads, cognitive-patterns |
| L3 | dormant-contexts, open-threads, switching-cost, alignment-check |

**Why this matters for AI consumers:**

When Claude calls `tunnel_state("auth-refactor")`, it doesn't just get prose — it gets synthesized output where every claim carries a citation back to the underlying conversation. The AI can't fabricate decisions because the citation chain is structural. `brain.resolve_citations(l3_id)` is a single SQL call that walks L3 → L2 → L1 → L0 and returns every source message.

**The .claude/skills/ pack.** Each of the 25 tools ships with a SKILL.md manifest declaring layer, reads, writes, citations, determinism, and explicit "Does NOT do" boundaries. This extends the AI-first README pattern: the AI doesn't just know *when* to call a tool, it gets a full contract per tool.

**Architecture:** Conversations normalized to Parquet (DuckDB). Embedded locally via fastembed or via OpenRouter. Vectors in LanceDB. Optional Supabase canonical backend for multi-tenant deployments (see [Migration 003](https://github.com/mordechaipotash/brain-mcp/blob/main/supabase/migrations/003_shelet_l0_to_l3.sql)).

**Setup:**

```
pipx install brain-mcp && brain-mcp setup
```

Auto-discovers sources, imports, embeds, configures MCP clients.

100% local by default, MIT licensed, macOS/Linux/Windows. The full ADR is at [docs/adr/001-shelet-reference-implementation.md](https://github.com/mordechaipotash/brain-mcp/blob/main/docs/adr/001-shelet-reference-implementation.md).

GitHub: https://github.com/mordechaipotash/brain-mcp
PyPI: https://pypi.org/project/brain-mcp/

Feedback on the SHELET framing especially welcome — particularly whether the L0→L3 stratification + structural citation discipline translates to other MCP servers.

---

## 3. r/adhdprogramming

**Title:** I have ADHD and kept losing my train of thought across AI conversations, so I built a "cognitive prosthetic" that remembers for me

**Body:**

You know that feeling when you KNOW you figured something out last week — you had the whole architecture in your head, you made decisions, you were in the zone — and now it's just... gone? And you're sitting there trying to reconstruct it from scratch, except you can't even remember which conversation it was in?

That's my life. I have ADHD, I use AI tools constantly (Claude, Cursor, Gemini), and my conversation history is basically a graveyard of brilliant context I can never find again.

So I built **brain-mcp** — it indexes all your AI conversations and makes them searchable. But the part that actually matters for ADHD brains:

**It reconstructs your mental state.**

Not just "here's a conversation from last Tuesday." Actual prosthetic tools:

- **tunnel_state** → "Here's where you were on the auth refactor: you'd decided on JWT, had 3 open questions about refresh tokens, and were about to test the middleware." Like loading a save game for your brain.
- **context_recovery** → full briefing when you're returning to something after hyperfocusing on something else for 4 days
- **switching_cost** → tells you what you'd lose by context-switching right now (sometimes seeing the cost is enough to stay on task)
- **open_threads** → finds all your unfinished work across conversations

I didn't build this as a productivity tool that happens to help with memory. I built it as a **cognitive prosthetic** that happens to be software. The difference matters to me.

**Setup:**

```
pipx install brain-mcp && brain-mcp setup
```

30 seconds. 100% local — nothing leaves your machine. Works with Claude, Cursor, Windsurf, Gemini CLI. MIT licensed.

GitHub: https://github.com/mordechaipotash/brain-mcp

It's not perfect — still refining some importers — but it's already changed how I work. Happy to answer questions.

---

## 4. X/Twitter Thread

**Tweet 1:**
Every AI conversation starts from zero. You explained your whole project last week? Gone.

I built brain-mcp — an open-source MCP server that gives your AI memory across conversations.

25 tools. 12ms recovery. 100% local.

Here's what makes it different 🧵

**Tweet 2:**
It's not just search. It's cognitive prosthetics.

`tunnel_state("auth-refactor")` → loads your "save game" — where you left off, open questions, last decisions.

Built by someone with ADHD who got tired of re-explaining context every single conversation.

**Tweet 3:**
The tools that matter most:

• tunnel_state — reconstruct your mental state
• switching_cost — quantify what you'd lose by context-switching
• context_recovery — full briefing after days away
• open_threads — find all your unfinished work

Not search. Prosthetics.

**Tweet 4:**
Here's a pattern I think should be way more common:

The README has a "For AI Assistants" section that teaches the AI WHEN to search and HOW to present results.

If your tool is used BY an AI, write docs FOR the AI.

→ brainmcp.dev/for-ai

**Tweet 5:**
Under the hood:
• Conversations normalized to Parquet
• Embedded locally via fastembed (no API keys)
• Stored in LanceDB
• MCP server over stdio
• Works with Claude, Cursor, Windsurf, Gemini CLI

Zero cloud dependencies. MIT licensed.

**Tweet 6:**
Setup takes 30 seconds:

pipx install brain-mcp && brain-mcp setup

Auto-discovers your conversation sources, imports, embeds, and configures your MCP clients.

macOS / Linux / Windows.

**Tweet 7:**
Still actively building — but it's been genuinely useful for my own workflow.

GitHub: github.com/mordechaipotash/brain-mcp
Site: brainmcp.dev
PyPI: pypi.org/project/brain-mcp/

If you use AI daily and lose context across conversations, give it a try. Feedback welcome.

---

## 5. Hacker News — Show HN

**Title:** Show HN: Brain-MCP – Query your AI conversation history with 25 MCP tools (all local)

**First comment:**

I use Claude, Cursor, and Gemini CLI daily. The biggest friction isn't the AI — it's that every conversation starts from zero. Context I built up in one session is gone in the next. I kept losing hours re-explaining project state.

brain-mcp indexes your AI conversation history and exposes it through 25 MCP tools. The architecture: conversations are normalized into Parquet files, embedded locally using fastembed (no API keys), and stored in LanceDB. The MCP server runs over stdio.

The tools I find most interesting aren't the search ones — they're what I call "prosthetic" tools. tunnel_state reconstructs your working context for a domain — where you left off, open questions, recent decisions. switching_cost quantifies what you'd lose by changing focus. context_recovery generates a full briefing when returning to something after days away. The idea is reconstructing mental state, not just finding old text.

One design choice worth discussing: the README has a "For AI Assistants" section at the top with instructions for the AI on when to proactively search and how to format results. If your tool's primary consumer is an AI, it makes sense to write documentation for that consumer. There's a dedicated page at brainmcp.dev/for-ai.

Setup: `pipx install brain-mcp && brain-mcp setup` — auto-discovers conversation sources, imports, embeds, and configures MCP clients. Currently supports Claude Desktop/Code, Cursor, Windsurf, and Gemini CLI. ChatGPT import exists but still needs work.

Everything runs locally. No cloud, no API keys for core functionality. MIT licensed. macOS/Linux/Windows.

https://github.com/mordechaipotash/brain-mcp
