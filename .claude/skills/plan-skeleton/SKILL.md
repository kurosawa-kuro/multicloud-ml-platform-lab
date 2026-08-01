---
name: plan-skeleton
description: Before implementing, fix the structure (files, interfaces, stubs, test outline) and a TDD contract, so the agent does not write production code before there is a verifiable shape to fill
---

# Plan Skeleton

Implements **Layer 6: Skeleton / TDD / Implementation** of `docs/specs/kurosawa-thin-harness-architecture.md` (§14). Repo conventions for skeleton-first work live in `docs/specs/skeleton-first-development.md` — that is the source of truth; this skill is the per-task procedure, do not duplicate its content.

The point: don't let the agent jump straight to production code. First fix *where things go* and *how we will know it works*.

## When to use

- Standard and Heavy tasks that add or change behavior, before `execute-task` / `reconcile-task`.
- Skip for Light tasks (docs, trivial reversible edits).

## Steps

1. **Skeleton** — name the file layout, interfaces / ports, usecase outline, and the fakes/stubs needed. No real logic yet.
2. **Test / Acceptance** — write the failing test(s), the acceptance criteria, and the exact verification command. Map acceptance to an Evidence Level (`verify-completion`).
3. **Minimal Implementation Plan** — the smallest change inside Scope and allowed paths that makes the test pass. Note the rollback trigger.
4. Fill the `docs/templates/tdd-contract.md` template and attach it to the task note.
5. Hand off to `execute-task` (single pass) or `reconcile-task` (if convergence over multiple attempts is expected).

## Rules

- Skeleton before implementation; structure before logic.
- Do not let TDD become test-fitting: tests encode acceptance intent, not the implementation. Never edit a test merely to make it pass (§14.5, §22.5).
- Mock only what the boundary requires; over-mocking hides that the real path is unverified — pair with Evidence Level ≥3 when the production path matters.
- Stay within allowed paths from the Change Boundary; a skeleton that needs a protected path is a Heavy-task / owner-approval signal.

## Output

- Skeleton (files / interfaces / stubs)
- Failing test + acceptance criteria + test command
- Minimal implementation plan
- Verification command + target Evidence Level
- Rollback trigger
