#!/usr/bin/env bash
# Stop: lightweight end-of-session bookkeeping (externalized from settings.json so the whole thing
# is one deletable file that respects the ${CLAUDE_PROJECT_DIR} convention like the other hooks).
# 1) append a session-end line to the (gitignored) session log.
# 2) insert a session-boundary marker into the decision log, but only if the tail is not already a
#    boundary — so idle sessions do not stack empty boundaries in the trade journal (Layer 9).
# NON-BLOCKING: always exit 0.
set -euo pipefail
root="${CLAUDE_PROJECT_DIR:-.}"
cd "$root" 2>/dev/null || exit 0
ts="$(date -u +%FT%TZ)"

mkdir -p .claude/logs
printf '%s session end\n' "$ts" >> .claude/logs/session.log

d="docs/decisions/decision-log.md"
if [ -f "$d" ]; then
  last="$(tail -n1 "$d" 2>/dev/null || true)"
  case "$last" in
    '--- session boundary'*) : ;;
    *) printf -- '\n--- session boundary %s ---\n' "$ts" >> "$d" ;;
  esac
fi
exit 0
