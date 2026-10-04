#!/usr/bin/env bash
# brain — CC SessionEnd hook: whole-file snapshot at the moment the transcript is FINAL.
# One clean authoritative capture per session. Atomic tmp+rename; exit 0 always.
set -uo pipefail

BRAIN_HOME="${BRAIN_HOME:-$HOME/.brain}"
LANE="cc_transcript"
SPOOL="$BRAIN_HOME/spool/$LANE"
mkdir -p "$SPOOL" "$BRAIN_HOME/health" 2>/dev/null || exit 0

# heartbeat FIRST — side effect, not report
: > "$BRAIN_HOME/health/hook-cc-sessionend.last_run" 2>/dev/null || true

input="$(cat)" || exit 0
tp="$(printf '%s' "$input" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("transcript_path",""))' 2>/dev/null)" || exit 0
sid="$(printf '%s' "$input" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null)" || exit 0
[ -n "$tp" ] && [ -n "$sid" ] && [ -f "$tp" ] || exit 0
# 2.1.1: name the transcript's project folder too — one session uuid can live in two
# project folders after a rename, and the drain must know which copy a delta extends.
proj="$(basename "$(dirname "$tp")")"
key="$sid@$proj"

tmp="$SPOOL/.$key.final.$$"
cp "$tp" "$tmp" 2>/dev/null && mv -f "$tmp" "$SPOOL/$key.jsonl" || rm -f "$tmp" 2>/dev/null
exit 0
