---
name: control-change
description: Classify a change discovered mid-task (out-of-scope work, broken assumption, new risk) instead of silently implementing it
---

# Control Change

Implements **Layer 5: Change Boundary** of `docs/specs/kurosawa-thin-harness-architecture.md` (§13) plus the scope-change branch of the Human Judgment Gate (§11.3). The repo's protected paths / allowed layers are in `docs/specs/change-boundary.md`.

When work reveals something outside the task's agreed Scope — extra refactors, renames, "while I'm here" fixes, a broken assumption, or a new risk — stop and classify it. Do not let `execute-task` silently absorb it; that is scope creep.

## When to use
Mid-execution, the moment you notice any of:
- a fix outside the task's Goal / Scope.
- a broken assumption the plan relied on.
- a newly required side-effect.
- docs and implementation contradicting each other.
- a refactor growing beyond the task's range.

## Steps

1. Name the change in one line: what it is and why it surfaced.
2. Classify it:
   - **in-scope** — already covered by the task; proceed.
   - **needs approval** — irreversible / external-effect / owner-only; stop and ask (see `scan-decisions`).
   - **follow-up task** — valid but separable; create via `create-task`, do not do it now.
   - **defer** — low value or uncertain; record and skip.
   - **reject** — out of scope and not worth a task; record why.
3. Record the change, its class, any assumption made, and the rollback / change trigger in the task note. For a real scope cut or accepted risk (defer / reject / risk-accepted / rollback), also append one entry via `log-decision` so the cross-task journal shows scope-creep and stop-loss behavior over time.
4. Only "in-scope" continues in the current diff. Everything else leaves it.

## Rules

- Default to follow-up / defer, not silent implementation.
- "While I'm here" refactors, renames, and commonization are scope creep unless the task says otherwise.
- A rejected or deferred change is still recorded, never silently dropped.

## Output

- Change + class (in-scope / approval / follow-up / defer / reject)
- Assumption + rollback / change trigger
- Follow-up task link if created
