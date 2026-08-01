#!/usr/bin/env bash
# PostToolUse (Edit|Write|MultiEdit): soft, NON-BLOCKING nudge when one session edits more
# distinct files than the Standard weight-class limit (8). False positives are tolerated — this
# is a Reconcile (Layer 7) scope-creep signal, not an enforcement. Fires once at the crossing.
# Emits additionalContext so the agent sees it; always exit 0 (never blocks).
set -euo pipefail
input="$(cat)"
f="$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty')"
sid="$(printf '%s' "$input" | jq -r '.session_id // "nosession"')"
[ -z "$f" ] && exit 0
dir="${CLAUDE_PROJECT_DIR:-.}/.claude/logs"
mkdir -p "$dir"
list="$dir/scope-$sid.list"
grep -qxF "$f" "$list" 2>/dev/null || printf '%s\n' "$f" >> "$list"
n="$(wc -l < "$list" | tr -d ' ')"
threshold=8
if [ "$n" -eq $((threshold + 1)) ]; then
  msg="detect-scope-creep: this session has edited $n files (> Standard limit $threshold). If this is one task, re-check the weight class (classify-task) and route extra changes through control-change / reconcile-task instead of widening scope."
  printf '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":%s}}\n' "$(printf '%s' "$msg" | jq -Rs .)"
fi
exit 0
