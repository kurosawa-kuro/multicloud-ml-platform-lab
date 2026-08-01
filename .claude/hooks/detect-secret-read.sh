#!/usr/bin/env bash
# PreToolUse (Bash): block shell commands that read local secret files.
# Layer 4/5 HARD complement (read side). permissions(settings.json) can deny the Read *tool*
# on env/secret.yaml, but a Bash command like `cat env/secret.yaml` or `grep x env/secret.yaml`
# bypasses that. This closes the read-side gap for the allow-listed read tools (rg/grep/head/
# tail/cat/less/awk/sed). exit 2 returns the message to the agent so it stops and asks the owner.
# Protected secret paths for this repo are declared in docs/specs/change-boundary.md.
set -euo pipefail
input="$(cat)"
cmd="$(printf '%s' "$input" | jq -r '.tool_input.command // empty')"
[ -z "$cmd" ] && exit 0
# Match references to secret material anywhere in the command line.
if printf '%s' "$cmd" | grep -Eq \
  '(^|[^a-zA-Z0-9_./-])(env/)?secret(\.[a-zA-Z0-9_-]+)?\.yaml|(^|[^a-zA-Z0-9_./-])\.env($|[^a-zA-Z0-9_.-])|(^|[^a-zA-Z0-9_./-])\.env\.[a-zA-Z0-9_-]+|DOPPLER_TOKEN|/secret/'; then
  echo "detect-secret-read: this command reads secret material (env/secret*.yaml / .env* / Doppler token / */secret/*). Reading secrets into the transcript is an owner-only boundary — stop and ask the owner, or use a redacted/derived value instead (docs/specs/change-boundary.md, Layer 4/5)." >&2
  exit 2
fi
exit 0
