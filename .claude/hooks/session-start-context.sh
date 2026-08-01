#!/usr/bin/env bash
# SessionStart: inject the live operating state into context at the start of every session.
# Closes the Layer 9 loop — distilled judgment memory and open tasks are otherwise only read if
# the agent happens to go look. This surfaces them automatically so each session starts already
# reminded of the standing decisions and what is in flight. NON-BLOCKING: emits additionalContext,
# always exit 0. Keep it SMALL (headlines only) so it does not bloat the always-on context.
set -euo pipefail
root="${CLAUDE_PROJECT_DIR:-.}"
cd "$root" 2>/dev/null || exit 0

section() {
  # $1 = heading, $2 = file, $3 = max lines
  [ -f "$2" ] || return 0
  local body
  body="$(grep -vE '^\s*(#|$)' "$2" 2>/dev/null | head -n "$3" || true)"
  [ -n "$body" ] && printf '\n## %s\n%s\n' "$1" "$body"
}

ctx=""
# Active + verifying tasks (filenames only — the agent opens the ones it needs).
# 03_active = hands-on; 04_verifying = built, awaiting a terminal signal (still in flight).
if [ -d docs/tasks/03_active ]; then
  active="$(find docs/tasks/03_active -maxdepth 1 -name '*.md' -printf '- %f\n' 2>/dev/null | head -n 20 || true)"
  [ -n "$active" ] && ctx+="$(printf '\n## Open tasks (docs/tasks/03_active)\n%s\n' "$active")"
fi
if [ -d docs/tasks/04_verifying ]; then
  verifying="$(find docs/tasks/04_verifying -maxdepth 1 -name '*.md' -printf '- %f\n' 2>/dev/null | head -n 20 || true)"
  [ -n "$verifying" ] && ctx+="$(printf '\n## Verifying — awaiting terminal signal (docs/tasks/04_verifying)\n%s\n' "$verifying")"
fi
# Distilled judgment memory (standing principles).
ctx+="$(section 'Distilled judgment memory (docs/memory/distilled-memory.md)' docs/memory/distilled-memory.md 30)"

[ -z "$ctx" ] && exit 0
msg="$(printf 'Session-start operating state for this repo. Treat as background reminders (they reflect what was true when written — verify before relying on any named path/flag):\n%s' "$ctx")"
printf '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":%s}}\n' "$(printf '%s' "$msg" | jq -Rs .)"
exit 0
