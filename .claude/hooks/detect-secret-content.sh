#!/usr/bin/env bash
# PreToolUse (Edit|Write|MultiEdit): block writes whose *content* looks like a live secret.
# Complement to detect-safety-boundary (which gates by PATH). This gates by CONTENT so a secret
# pasted into a normally-innocent file (README, a test fixture, a config) is caught before it is
# written. Layer 5 Change Boundary, content side. exit 2 returns the message so the agent stops.
# Patterns are shape-based (prefix + charset + length) to avoid matching ordinary prose; extend
# them for the credential shapes your project actually handles. NEVER hard-codes a real secret.
set -euo pipefail
input="$(cat)"
# Edit/Write use .content or .new_string; MultiEdit uses .edits[].new_string.
content="$(printf '%s' "$input" | jq -r '
  [ .tool_input.content // empty,
    .tool_input.new_string // empty,
    ( .tool_input.edits // [] | map(.new_string // empty) | join("\n") )
  ] | join("\n")')"
[ -z "$content" ] && exit 0

hit=""
# Always returns 0 (the `if` swallows grep's no-match exit 1) so `set -e` never aborts mid-scan.
match() { if printf '%s' "$content" | grep -Eq -- "$1"; then hit="$2"; fi; }

# AWS access key id (AKIA/ASIA + 16 base32-ish chars)
match '(AKIA|ASIA)[0-9A-Z]{16}' 'AWS access key'
# GitHub tokens (ghp_/gho_/ghu_/ghs_/ghr_ + 30+)
match 'gh[posur]_[0-9A-Za-z]{30,}' 'GitHub token'
# OpenAI-style secret key
match 'sk-[0-9A-Za-z_-]{20,}' 'API secret key (sk-...)'
# Slack token
match 'xox[baprs]-[0-9A-Za-z-]{10,}' 'Slack token'
# Google API key
match 'AIza[0-9A-Za-z_-]{35}' 'Google API key'
# Doppler service/personal token
match 'dp\.(st|pt)\.[0-9A-Za-z]{10,}' 'Doppler token'
# Private key blocks
match '\-\-\-\-\-BEGIN [A-Z ]*PRIVATE KEY\-\-\-\-\-' 'private key block'
# Generic bearer/authorization literal with a long opaque value
match '[Bb]earer [0-9A-Za-z._-]{24,}' 'hard-coded bearer token'

if [ -n "$hit" ]; then
  echo "detect-secret-content: the content being written matches a secret shape ($hit). Do NOT commit live credentials — use a placeholder (example/changeme/your-token), env/secret.yaml (gitignored), or Doppler, and rotate the value if it was real. Stop and confirm with the owner (docs/specs/change-boundary.md + .claude/rules/security.md, Layer 5)." >&2
  exit 2
fi
exit 0
