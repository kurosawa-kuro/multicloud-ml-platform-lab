#!/usr/bin/env bash
# PreToolUse (Edit|Write|MultiEdit): block edits to protected paths and route to owner approval.
# Layer 5 Change Boundary HARD complement: permissions(settings.json) stop Bash commands, but do
# NOT stop Edit/Write to a sensitive file path. This closes that gap. exit 2 returns the message
# to the agent so it stops and asks. Protected paths instantiated for this repo are in
# docs/specs/change-boundary.md (env/secret, infra, terraform, CI workflows).
set -euo pipefail
input="$(cat)"
f="$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty')"
[ -z "$f" ] && exit 0
case "$f" in
  env/secret/*|*/env/secret/*|\
  infra/*|*/infra/*|\
  terraform/*|*/terraform/*|\
  .github/workflows/*|*/.github/workflows/*)
    echo "detect-safety-boundary: '$f' is a protected path (secret / infra / terraform / CI workflow). This is an owner-only / Heavy-task boundary — stop and get owner approval before editing it (docs/specs/change-boundary.md, Layer 5)." >&2
    exit 2 ;;
esac
exit 0
