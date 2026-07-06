[![MseeP.ai Security Assessment Badge](https://mseep.net/pr/mordechaipotash-brain-mcp-badge.png)](https://mseep.ai/app/mordechaipotash-brain-mcp)

# brain-mcp v1.0

**Transportable AI memory, now shipping as a distributable.**

*Your AI conversation history, queryable from any MCP-aware LLM, stored entirely on your machine.*

[![Stars](https://img.shields.io/github/stars/mordechaipotash/brain-mcp?style=flat-square)](https://github.com/mordechaipotash/brain-mcp/stargazers)
[![PyPI](https://img.shields.io/pypi/v/brain-mcp?style=flat-square&logo=pypi&logoColor=white)](https://pypi.org/project/brain-mcp/)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)

---

## What changed in v1.0

brain-mcp v0.x was a hosted SHELET reference implementation pointed at a Supabase substrate. **v1.0 is a thin MCP server that wraps the [Bob protocol](https://apiiam.com/bob/init.0).** Storage moved from cloud Postgres to local parquets under `~/.bob/`. The protocol is free; brain-mcp is one implementation; a paid `bob managed *` tier (cloud sync, recipes) is opt-in and lives in a separate package.

The 53 of you who starred v0.x: thank you. v1.0 is the version we were always going to build — *memory belongs to the rememberer is the architecture that makes the product possible*, not just the pitch.

### What v1.0 inherits
- The 8-tool surface (`bob_search`, `bob_recent`, `bob_what_do_i_think`, `bob_tunnel_state`, `bob_thinking_trajectory`, `bob_open_threads`, `bob_conversations_by_date`, `bob_health`).
- The "AI assistant readme" frame — your LLM reads this README and gets dramatically more useful.
- The cite-or-die discipline — every claim from brain-mcp surfaces a `conv_id`, `ts`, and `src` so the LLM can show its work.

### What v1.0 drops
- Cloud database connection (no Supabase, no anon keys).
- Semantic search (no embeddings yet; v1.0 is keyword-only — embeddings are a separate future protocol).
- The `brainmcp.dev` hosted demo dashboard (the protocol is the spec now, not a website).

---

## Install (2 minutes)

### Step 1 — Install Bob

In any LLM session that can fetch URLs (Claude Code, Claude.ai with web search, ChatGPT with browse, Cursor, etc.), paste:

```
Run this: https://apiiam.com/bob/init.0
```

The LLM fetches the XML protocol, runs duckdb against `~/.claude/projects/`, `~/.codex/sessions/`, your Cursor sqlite, and any ChatGPT / Claude.ai exports you've downloaded. Writes `~/.bob/turns.parquet` (~5 min, ~30MB for an active user). No passwords, no signups, no network calls beyond the initial spec fetch.

Optional — promote L0 → L1 (noise filter):

```
Run this: https://apiiam.com/bob/init.1
```

Strips system-reminders, command outputs, build-log pastes. Writes `~/.bob/turns.l1.parquet` + audit trail.

### Step 2 — Install brain-mcp

```bash
uvx brain-mcp                    # ephemeral, no install
# OR
uv tool install brain-mcp        # persistent CLI
# OR
pipx install brain-mcp
```

### Step 3 — Register with your LLM client

**Claude Code / Claude Desktop** — add to `~/.claude/mcp.json` (or via `claude mcp add`):

```json
{
  "mcpServers": {
    "brain": {
      "command": "uvx",
      "args": ["brain-mcp"]
    }
  }
}
```

**Cursor / Continue / other MCP clients** — same shape, see their MCP docs.

That's it. Open a new LLM session and ask: *"what was I working on last week?"*

---

## 🤖 For AI Assistants

*You're reading this because your human installed brain-mcp and gave you access to their AI conversation history. Here's how to use it well.*

### When to call which tool

| Your human says... | You should call... | Why |
|---|---|---|
| "Where did I leave off with..." | `bob_tunnel_state(domain)` | Reconstructs their mental save-state for that domain — most recent activity across the top conversations |
| "What do I think about..." | `bob_what_do_i_think(topic)` | Filters to *user-role* turns only, deduplicated. Their voice, not the AI's reframe. |
| "I was working on something about..." | `bob_search(query)` | Substring match across both roles. Returns up to 12 most recent matches. |
| "What was I doing yesterday / last week?" | `bob_recent(hours=24)` or `bob_conversations_by_date(date)` | Time-based browse. |
| "I keep forgetting to..." | `bob_open_threads()` | Surfaces turns with TODO / `?` / blocked / waiting-on. |
| "How did my thinking on X evolve?" | `bob_thinking_trajectory(topic)` | User-turns in *ascending* time order. Read left-to-right. |
| *Starting any new topic* | `bob_search(topic)` | Check if they've thought about it before. |
| *Uncertain what's available* | `bob_health()` | Reports active layer (L1 if filtered, else L0), per-source counts, manifest. |

### What brain-mcp does NOT do
- **No semantic / embedding search.** v1.0 is keyword-only. Don't claim "I searched semantically" — claim "I searched the keyword X across their conversations." For sentences that should match semantically, try multiple keyword angles.
- **No cross-LLM identity stitching.** A conversation in ChatGPT and one in Claude on the same topic are two `conv_id`s. Surface both; don't merge.
- **No future-looking inference.** The data is what they typed. Don't speculate "they're probably about to X" — say "in conversation Y on date Z they said Q."
- **No write-back.** brain-mcp is strictly read-only on the parquets.

### Cite-or-die discipline
Every fact you state from brain-mcp must include the `ts`, `src`, and `conv_id` from the row(s) it came from. Don't paraphrase what they said without telling them where you read it. "In your conductor-role-orchestra conversation on 2026-05-23, you wrote: *...*" — not "you believe X."

---

## 👤 For Humans

### What brain-mcp does
You've been chatting with AI for months or years across multiple tools (ChatGPT, Claude, Cursor, Codex, Claude Code). Each tool only sees its own slice. **Bob captures all five into one local file. brain-mcp lets any MCP-aware LLM read that file.**

The first time you ask a fresh LLM session *"what was I working on last week?"* and it answers with conversations from three different tools — that's the unlock.

### What brain-mcp does NOT do
- **It does not phone home.** brain-mcp opens no network sockets. Your data stays on your machine. (Verify: run `lsof -p <brain-mcp-pid>` while it's serving.)
- **It does not need an account.** No signup, no API keys.
- **It does not generate embeddings.** v1.0 is keyword search only. Embeddings are a separate (also local) future protocol.
- **It does not modify your AI conversation logs.** brain-mcp reads `~/.bob/turns.parquet`, which bob produced from your CC / Codex / Cursor / export files. The original logs are untouched.

### Storage layout

```
~/.bob/
├── turns.parquet          ← L0: raw turns from up to 5 sources (init.0 output)
├── turns.l1.parquet       ← L1: noise-filtered (init.1 output; optional)
├── turns.l1.dropped.parquet ← L1 audit trail (which turns were filtered, why)
└── turns.l1.manifest.json ← counts + drop reasons (also optional)
```

brain-mcp prefers L1 when present, falls back to L0.

### Refresh

Re-run init.0 in any LLM session. It overwrites `~/.bob/turns.parquet` with the latest 30 days. No cron, no daemon — you choose when.

---

## Architecture

- **Protocol:** [apiiam.com/bob/init.0](https://apiiam.com/bob/init.0) (XML, fetched by your LLM)
- **Storage:** parquet files under `~/.bob/` (or `$BOB_HOME`)
- **Query engine:** duckdb (in-process, in-memory, read-only against parquets)
- **MCP server:** Python + FastMCP, stdio transport
- **Optional service tier:** `bob managed *` subcommands for cloud sync (separate package, opt-in, lives at a different binary — not bundled here)

```
Your LLM (Claude / ChatGPT / Cursor / ...)
        ↓ MCP stdio
brain-mcp (this package)
        ↓ duckdb read_parquet
~/.bob/turns.l1.parquet  (or L0 if no L1)
        ↑ written by
init.0 / init.1 (Bob protocol, run in an LLM session)
        ↑ reads
your raw AI conversation logs (CC / Codex / Cursor / exports)
```

No box in this diagram makes a network call after install except `init.0` fetching the protocol URL once.

---

## v0.x compatibility

If you registered the v0.x `brain-mcp` against a Supabase substrate: that config no longer works. v1.0 reads local parquets only. To migrate:

1. Run https://apiiam.com/bob/init.0 to produce `~/.bob/turns.parquet`.
2. Upgrade: `uv tool upgrade brain-mcp` (or `pipx upgrade brain-mcp`).
3. Restart your LLM client (it re-spawns the MCP server with the new code).

Your old `BRAIN_MCP_DB_URL` / `BRAIN_MCP_API_KEY` env vars are now ignored. Remove them.

---

## License

MIT. Use anywhere, including commercially. No warranty.

## Links

- Protocol: https://apiiam.com/bob/init.0
- Author: Mordechai Potash — [mordechaipotash.com](https://mordechaipotash.com)
- Essays on the underlying thesis: [daf.mordechaipotash.com](https://daf.mordechaipotash.com)
- Issues / discussion: [GitHub](https://github.com/mordechaipotash/brain-mcp/issues)
