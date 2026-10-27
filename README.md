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
the whole session, subagent transcripts and saved tool output included, byte-exact,
content-hashed, locally — makes it queryable with citations you can verify with `sed` and
`shasum`, tells you what was deleted, and puts it back.

## If Claude Code deleted it

```bash
brain-mcp health          # the receipt: "3 session(s) and 41 file(s) are gone from where the
                          #   agent kept them; the floor holds every one" — each re-hashed
                          #   against the manifest when the recorder first noticed it
brain-mcp restore --list  # what can be put back
brain-mcp restore <id>    # writes the session (and its folder) back, byte-exact, then:
                          #   cd <its working directory> && claude --resume <id>
```

`restore` never overwrites different bytes (identical bytes are a no-op), is all-or-nothing,
and takes `--dry-run` and `--to <dir>`. It is a CLI verb only — no MCP tool writes outside
the floor.

## Install

```bash
pipx install brain-mcp           # or: uvx brain-mcp
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
  snapshot); Codex, Pi, and everything inside a Claude Code session's own folder (subagent
  and workflow transcripts, `.meta.json`, `tool-results/`) via a 60-second poll-scan. One
  spool, one floor.
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
- **One network fetch, ever, and it is not about you.** No telemetry, no cloud, no
  accounts, and no HTTP client in this package. The exception: DuckDB downloads its
  full-text-search extension (`fts`) from DuckDB's own extension repository the first time
  the index is built, and caches it under `~/.duckdb`. `brain-mcp install` does that fetch
  up front and says so; `brain-mcp doctor` reports whether it is cached.

## The 7 MCP tools

| tool | what |
|---|---|
| `brain_search` | BM25 over everything recorded; cited hits or an explicit abstention |
| `brain_get` | the raw lines behind a citation, with sha verification |
| `brain_recent` | time-ordered recent activity, every row cited |
| `brain_sessions` | session cards per day/agent |
| `brain_health` | per-lane origin-vs-floor freshness (unknown ≠ healthy), plus the receipt of what the agents deleted |
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
- "What do I think about X" → `brain_search(query, role="user")`. That returns what the
  PERSON wrote: text the harness injected under the user role (skill bodies, reminders,
  slash-command wrappers, compaction summaries, Codex context blocks — a quarter to two
  thirds of "user" rows, measured) is excluded unless you pass `include_machine=True`, and
  subagent transcripts unless `include_subagents=True`. "How did my thinking evolve" → add
  `order="time_asc"`; bound it with `since` / `until`. The server has no opinion about your
  human's mind; it has their words, with receipts.

## The floor format

```
~/.brain/
  spool/<lane>/                 hooks + scanner write here (atomic, dot-tmp invisible)
  lake/<lane>/<session>.jsonl   THE FLOOR: append-only, byte-identical to the origin;
                                a rewrite opens <session>.g2.jsonl — old kept, never deleted
  lake/cc_sessiondir/<project>/<session>/...   a CC session's folder, mirrored at the
                                same relative paths (subagents/, tool-results/, *.meta.json)
  manifest/manifest.jsonl       versioned lines: a chunk's byte range, line range and sha256; events
                                (origin_gone, restored, rewrite_capped, import)
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
brain-mcp doctor                  # capture status, health, fts cache, the receipt, runaway generations
brain-mcp restore [<id>] [--list] [--to DIR] [--dry-run]   # put a deleted session back
brain-mcp redact <file> --lines A B --reason "..."   # tombstone a secret; audited in manifest
brain-mcp migrate-v1 <all_conversations.parquet>     # import v1 data (marked v1_derived)
brain-mcp import-chatgpt <export.zip|conversations.json|dir>   # a ChatGPT export → the chatgpt_export lane
brain-mcp uninstall               # removes hooks + scheduler; your floor is KEPT
```

## ChatGPT

ChatGPT keeps no local session files, so there is nothing for a lane to watch. Its history
enters through a one-time export instead:

1. ChatGPT → Settings → Data Controls → Export Data, then download the emailed ZIP.
2. `brain-mcp import-chatgpt ~/Downloads/chatgpt-export.zip` — the ZIP is read in place, nothing
   is extracted. Large accounts are split into `conversations-000.json`, `-001.json`, and so on;
   all of them are read. An already unzipped folder (with the `conversations*.json` files at its top level) or a single
   `conversations.json` works too.

The converter writes one `<conversation_id>.jsonl` per conversation into
`~/.brain/imports/chatgpt/` — a manufactured origin the `chatgpt_export` watch lane then
captures like any other. Re-running over an unchanged export rewrites nothing, so it is safe
to repeat after each new export; only new and changed conversations move.

The export's `mapping` is a tree: a regenerated answer is a sibling branch, not a
replacement. Every message node is emitted, ordered by its own clock, with `parent_id`
preserved — so no branch is silently dropped and any root-to-leaf path stays reconstructable.

### Where an import came from

The converted file is not your original bytes, so each import also leaves one `import` event in
the manifest: the name, size and sha256 of the export (the ZIP, and every `conversations*.json`
inside it), the conversation count, and the converter version. To check a ChatGPT citation back
to your own export:

```bash
sha256sum ~/Downloads/chatgpt-export.zip      # the export, as you hold it
unzip -p ~/Downloads/chatgpt-export.zip conversations-000.json | sha256sum   # a member
grep '"event":"import"' ~/.brain/manifest/manifest.jsonl    # both hashes are on record
```

Then look the conversation id (the file name under `lake/chatgpt_export/`) up in that
`conversations*.json`. The manifest is an event log: a re-import after another import is recorded
again, an immediate repeat of the same one is not. The event states what was written into the
origin; the scan that follows decides what the floor holds (the CLI warns if a file hit the
rewrite cap, which a changed conversation title can trigger).

## v1 → v2

v2 is a rebuild around one principle: **capture the bytes first; derive everything else.**
v1 parsed conversations into a parquet and discarded the originals — v2's floor makes that
structurally impossible. v1's 25 tools became 7: the synthesis tools ("cognitive patterns",
"switching cost") are gone because a claim that can't carry a line-span citation isn't one
this server makes. Migration: `brain-mcp migrate-v1` — v1 rows are kept, marked as derived,
and floor-backed rows win wherever the source still exists.

## Scheduling

`brain-mcp install` writes a 60-second LaunchAgent on macOS. On Linux it does **not** install
anything — it prints a reminder, and the timer is yours to create. Without it the hooks keep
filling the spool and nothing ever drains it, so `brain-mcp health` goes stale while capture
looks fine. The two units:

```ini
# ~/.config/systemd/user/brain-mcp-record.service
[Unit]
Description=brain-mcp capture tick (drain spool, scan lanes, refresh index)

[Service]
Type=oneshot
ExecStart=%h/.local/bin/brain-mcp record
TimeoutStartSec=600
Nice=10
IOSchedulingClass=idle
```

```ini
# ~/.config/systemd/user/brain-mcp-record.timer
[Unit]
Description=Run brain-mcp capture tick every 60 seconds

[Timer]
OnBootSec=90s
OnUnitActiveSec=60s
AccuracySec=5s
Unit=brain-mcp-record.service

[Install]
WantedBy=timers.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now brain-mcp-record.timer
loginctl enable-linger "$USER"   # keep recording when you are not logged in
```

Windows: out of scope for v2.0.

MIT.
