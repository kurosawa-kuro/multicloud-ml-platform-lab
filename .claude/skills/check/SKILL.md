---
name: check
description: Run this project's quality gate (fmt / lint / test) and report real pass/fail with the actual command output, before calling work done or committing. Detects the toolchain from the repo; never claims green without having run it.
---

# Check

Run the project's quality gate and report the **actual** result. This is the mechanical floor under `verify-completion` (Layer 8): fmt clean, lint clean, tests green — observed, not asserted.

## When to use

- Before `commit`, before moving a task to `docs/tasks/05_done/`, and after any change that touches source.
- Not a substitute for `verify-completion` — that judges whether the Goal was met at the required Evidence Level. `check` only proves the gate passes.

## Steps

1. **Prefer the project's own entrypoint** if present — `make check`, else `make fmt lint test`. A Makefile is the source of truth for how this repo runs its gate.
2. Otherwise detect the toolchain and run its gate:
   - Rust: `cargo fmt --check` · `cargo clippy -- -D warnings` · `cargo test`
   - Python: `ruff format --check .` · `ruff check .` · `mypy .` (if configured) · `pytest`
   - Node: `npm run lint` · `npm test`
   - Go: `gofmt -l .` · `go vet ./...` · `go test ./...`
   - Shell: `bash -n` each script (and `shellcheck` if available)
3. Run every relevant gate — do not stop at the first pass. Fmt-clean but test-red is still red.
4. **Report the real outcome**: which commands ran, pass/fail each, and the failing output verbatim if red. If a gate was skipped (tool absent, not configured), say so and why — never silently drop it.

## Rules

- Never report green from a claim, a docs checkmark, or "it should pass". Green means the command was run this session and exited 0.
- If a gate fails, stop and fix or surface it — do not proceed to commit/done on red.
- If the repo has no gate yet, that is a finding: note it and propose the minimal `make check` for this stack.
