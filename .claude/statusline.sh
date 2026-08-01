#!/usr/bin/env bash
# statusLine: one-line status shown under the prompt. Surfaces the two things this harness cares
# about at a glance — how many tasks are in flight, and which git branch you are on — plus the
# model. Reads the status JSON Claude Code pipes in on stdin. Pure read-only, always exit 0.
# This is cosmetic; delete the "statusLine" block in settings.json (and this file) if you do not
# want it. Keep it FAST (no network, no heavy git) — it runs on every render.
set -euo pipefail
input="$(cat)"

model="$(printf '%s' "$input" | jq -r '.model.display_name // .model.id // "claude"')"
cwd="$(printf '%s' "$input" | jq -r '.workspace.current_dir // .cwd // "."')"
proj="$(basename "$cwd" 2>/dev/null || echo '?')"

branch=""
if git -C "$cwd" rev-parse --abbrev-ref HEAD >/dev/null 2>&1; then
  branch="$(git -C "$cwd" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
fi

active=0
if [ -d "$cwd/docs/tasks/03_active" ]; then
  active="$(find "$cwd/docs/tasks/03_active" -maxdepth 1 -name '*.md' 2>/dev/null | wc -l | tr -d ' ')"
fi

out="📁 ${proj}"
[ -n "$branch" ] && out="${out}  ⎇ ${branch}"
out="${out}  ✎ ${active} active"
out="${out}  ⟐ ${model}"
printf '%s\n' "$out"
exit 0
