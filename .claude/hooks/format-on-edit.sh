#!/usr/bin/env bash
# PostToolUse (Edit|Write|MultiEdit): best-effort auto-format of the single edited file.
# Keeps diffs clean without the agent having to remember `make fmt`. Stack-agnostic: it only acts
# when the matching formatter is actually installed, and NEVER fails the tool (always exit 0), so a
# project that lacks a given toolchain is silently a no-op. This is the first thing to delete
# downstream if it gets in the way (it is automation, not a safety control).
set -euo pipefail
input="$(cat)"
f="$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty')"
[ -z "$f" ] || [ ! -f "$f" ] && exit 0

have() { command -v "$1" >/dev/null 2>&1; }

case "$f" in
  *.rs)
    have rustfmt && rustfmt --edition 2021 "$f" >/dev/null 2>&1 || true ;;
  *.py)
    if have ruff; then ruff format "$f" >/dev/null 2>&1 || true
    elif have black; then black -q "$f" >/dev/null 2>&1 || true; fi ;;
  *.js|*.jsx|*.ts|*.tsx|*.json|*.css|*.md|*.yaml|*.yml)
    have prettier && prettier --write "$f" >/dev/null 2>&1 || true ;;
  *.tf)
    have terraform && terraform fmt "$f" >/dev/null 2>&1 || true ;;
  *.go)
    have gofmt && gofmt -w "$f" >/dev/null 2>&1 || true ;;
  *.sh)
    have shfmt && shfmt -w "$f" >/dev/null 2>&1 || true ;;
esac
exit 0
