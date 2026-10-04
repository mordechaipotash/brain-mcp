#!/usr/bin/env bash
# brain — CC Stop hook: spool the turn DELTA (thin-streamer: read, count, copy, advance).
#
# Ported from a production pair with two measured defects fixed:
#   1. The original's heartbeat lines sat AFTER `exit 0` — unreachable, so the hooks
#      never self-reported and looked alive while dead. v2: the heartbeat is a
#      side-effect file touch, FIRST thing — health reads its mtime, never a report.
#   2. The original silently stopped forever if a transcript shrank (offset > lines).
#      v2: a shrink resets the offset and ships the whole file as a rewrite snapshot.
#
# Contract: delta only, complete lines only (wc -l counts complete lines — a torn
# trailing line ships next turn), atomic write (dot-tmp + mv), full session id in the
# filename, exit 0 ALWAYS — a streamer failure must never block the agent.
set -uo pipefail

BRAIN_HOME="${BRAIN_HOME:-$HOME/.brain}"
LANE="cc_transcript"
SPOOL="$BRAIN_HOME/spool/$LANE"
OFFS="$BRAIN_HOME/offsets/$LANE"
mkdir -p "$SPOOL" "$OFFS" "$BRAIN_HOME/health" 2>/dev/null || exit 0

# heartbeat FIRST — a side effect, not a report (defect-1 fix)
: > "$BRAIN_HOME/health/hook-cc-stop.last_run" 2>/dev/null || true

input="$(cat)" || exit 0
tp="$(printf '%s' "$input" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("transcript_path",""))' 2>/dev/null)" || exit 0
sid="$(printf '%s' "$input" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null)" || exit 0
[ -n "$tp" ] && [ -n "$sid" ] && [ -f "$tp" ] || exit 0
# 2.1.1: name the transcript's project folder too — one session uuid can live in two
# project folders after a rename, and the drain must know which copy a delta extends.
proj="$(basename "$(dirname "$tp")")"
key="$sid@$proj"

total="$(wc -l < "$tp" | tr -d ' ')" || exit 0
last=0
[ -f "$OFFS/$key" ] && last="$(head -1 "$OFFS/$key" | tr -dc '0-9')" && last="${last:-0}"

if [ "$total" -lt "$last" ]; then
    # rewrite/truncation (defect-2 fix): reset and ship the whole file as a snapshot
    tmp="$SPOOL/.$key.rewrite.$$"
    cp "$tp" "$tmp" 2>/dev/null && mv -f "$tmp" "$SPOOL/$key.jsonl" && echo "$total" > "$OFFS/$key"
    exit 0
fi
[ "$total" -gt "$last" ] || exit 0

tmp="$SPOOL/.$key.turn.$$"
if tail -n "+$((last + 1))" "$tp" | head -n "$((total - last))" > "$tmp" 2>/dev/null; then
    # spool-rename FIRST, offset-advance SECOND (crash between = duplicate, absorbed)
    mv -f "$tmp" "$SPOOL/$key.turn-$last-$total.jsonl" && echo "$total" > "$OFFS/$key"
else
    rm -f "$tmp" 2>/dev/null
fi
exit 0
