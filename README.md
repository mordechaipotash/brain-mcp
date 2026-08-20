<!-- mcp-name: io.github.mordechaipotash/brain-mcp -->

# brain-mcp — the recorder for your AI conversations

**Your AI history is being deleted right now.** Claude Code deletes session files older
than `cleanupPeriodDays` (default **30**) at startup. Run this and see your own cliff edge:

```bash
# macOS
find ~/.claude/projects -name '*.jsonl' -exec stat -f '%Sm  %N' -t '%Y-%m-%d' {} + | sort | head -3
# Linux
find ~/.claude/projects -name '*.jsonl' -printf '%TY-%Tm-%Td %p\n' | sort | head -3
```

The oldest date you see is where your history ends. brain-mcp records it before it goes —
byte-exact, content-hashed, locally — and makes it queryable with citations you can verify
with `sed` and `shasum`.

## Install

```bash
pipx install brain-mcp --pre     # or: uvx brain-mcp
brain-mcp install cc             # CC hooks + 60-second scheduler
brain-mcp serve                  # the MCP server (stdio) — add to your client config
```

Or as a Claude Code plugin (hooks + server in one step):

```
/plugin marketplace add mordechaipotash/brain-marketplace
/plugin install brain
```

## What it does

- **Captures at source.** Claude Code via Stop/SessionEnd hooks (line-deltas + a final
  snapshot); Codex and Pi via a 60-second poll-scan. One spool, one floor.
- **Keeps the bytes.** The floor is `~/.brain/lake/<lane>/<session>.jsonl` — append-only,
  byte-identical to the original, with an append-only sha256 manifest. Nothing is parsed
  and discarded; the DuckDB index is a cache, fully re-derivable from the lake.
- **Cites or abstains.** Every search hit carries `{file, line span, sha256}` — checkable
  by anyone with `sed -n 'A,Bp' file | shasum -a 256`, no database required. Zero hits
  return an explicit abstention naming the lanes and dates actually searched.
- **Reports health honestly.** `fresh` = the floor holds everything the agent's files show;
  `stale` = the agent has content the recorder missed; **`unknown` = unmeasured, not
  healthy** — never folded into "looks fine".
- **Backs up verifiably.** `brain-mcp backup <dest>` syncs lake+manifest and re-hashes
  sampled files at the destination. The verify can fail; that is the point.
- **Zero network calls at runtime.** No telemetry, no cloud, no accounts. Verify it:
  there is no httpx/requests import in this package.

## The 7 MCP tools

| tool | what |
|---|---|
| `brain_search` | BM25 over everything recorded; cited hits or an explicit abstention |
| `brain_get` | the raw lines behind a citation, with sha verification |
| `brain_recent` | time-ordered recent activity, every row cited |
| `brain_sessions` | session cards per day/agent |
| `brain_health` | per-lane origin-vs-floor freshness; unknown ≠ healthy |
| `brain_capture_status` | is the machinery itself moving (spool, heartbeats, floor) |
| `brain_backup` | verified backup; writes only outside the floor |

## 🤖 For AI Assistants

*You're reading this because your human runs THE RECORDER and gave you access to their
AI history. This is the honesty contract.*

**The one rule: everything you state from these tools is either CITED or ABSTAINED —
there is no third state.** A cited claim carries a checkable pointer (file + line span +
sha256); verify it with `brain_get(expect_sha256=...)` before building on it. An
abstention means "not found above threshold in the lanes and dates the tool measured" —
it does NOT mean "it never happened". Never fill an abstention with your own guess.

- Present recall as their words, dated: *"On 2026-08-19 you wrote: '…' (sess-7f2a.jsonl:412)"* —
  never as your own knowledge. One claim, one citation.
- `verified: false` from brain_get means the floor changed since indexing. Say so plainly.
- A health response containing any `unknown` lane is never "everything looks fine".
  The honest sentence is: "2 lanes fresh, 1 stale, 1 unmeasured."
- "What do I think about X" → `brain_search(query, role="user")`. "How did my thinking
  evolve" → add `order="time_asc"` and read the citations in time order. The server has
  no opinion about your human's mind; it has their words, with receipts.

## The floor format

```
~/.brain/
  spool/<lane>/                 hooks + scanner write here (atomic, dot-tmp invisible)
  lake/<lane>/<session>.jsonl   THE FLOOR: append-only, byte-identical to the origin;
                                a rewrite opens <session>.g2.jsonl — old kept, never deleted
  manifest/manifest.jsonl       one versioned line per chunk: byte range, line range, sha256
  offsets/<lane>/<session>      hook fast-path line counters
  health/*.last_run             side-effect heartbeats (mtimes are the proof, never a report)
  brain.duckdb                  the index — a cache, re-derivable from lake/ + manifest/
```

Where your agents keep their transcripts: Claude Code `~/.claude/projects/**/*.jsonl`
(rolling window!), Codex `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`, Pi
`~/.pi/agent/sessions/**/*.jsonl`.

## Other verbs

```bash
brain-mcp record                  # one capture tick (the scheduler runs this every 60s)
brain-mcp health [--exit-nonzero-on-stale]   # cron-able
brain-mcp doctor                  # capture status + health summary
brain-mcp redact <file> --lines A B --reason "..."   # tombstone a secret; audited in manifest
brain-mcp migrate-v1 <all_conversations.parquet>     # import v1 data (marked v1_derived)
brain-mcp uninstall               # removes hooks + scheduler; your floor is KEPT
```

## v1 → v2

v2 is a rebuild around one principle: **capture the bytes first; derive everything else.**
v1 parsed conversations into a parquet and discarded the originals — v2's floor makes that
structurally impossible. v1's 25 tools became 7: the synthesis tools ("cognitive patterns",
"switching cost") are gone because a claim that can't carry a line-span citation isn't one
this server makes. Migration: `brain-mcp migrate-v1` — v1 rows are kept, marked as derived,
and floor-backed rows win wherever the source still exists.

Windows: out of scope for v2.0. Scheduling is LaunchAgent (macOS) / systemd user timer (Linux).

MIT.
