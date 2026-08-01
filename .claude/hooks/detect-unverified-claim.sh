#!/usr/bin/env bash
# Stop: best-effort reminder that completion needs Evidence Level >=2 (Layer 8).
# Mechanically detecting an "unverified claim" in assistant text is not possible from a hook, so
# this uses a proxy: if a task note under docs/tasks/05_done/ changed in the working tree this
# session, a task was likely just closed — remind to confirm verify-completion produced real
# evidence. NON-BLOCKING (exit 0): visible in the transcript, not enforced.
set -euo pipefail
root="${CLAUDE_PROJECT_DIR:-.}"
cd "$root" 2>/dev/null || exit 0
changed="$(git status --porcelain docs/tasks/05_done/ 2>/dev/null | head -n1 || true)"
if [ -n "$changed" ]; then
  echo "detect-unverified-claim: a task note under docs/tasks/05_done/ changed this session. Done requires Evidence Level >=2 (real test / runtime / DB outcome), never a claim — confirm verify-completion was run before closing (docs/specs/evidence-policy.md, Layer 8)." >&2
fi
exit 0
