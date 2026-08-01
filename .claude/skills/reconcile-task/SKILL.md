---
name: reconcile-task
description: Run multi-attempt work as a bounded reconcile loop (observe → diff → plan → apply → verify) with hard stop conditions, so the agent converges desired and observed state instead of widening scope until something passes
---

# Reconcile Task

Implements **Layer 7: Reconcile Controller** of `docs/specs/kurosawa-thin-harness-architecture.md` (§15). Treat work as closing the gap between a desired state and the observed state — not a one-shot edit, and not an open-ended "keep trying" loop. The stop conditions are the whole point: a reconcile loop without them turns into scope-creep / nanpin.

## When to use

- When the work needs more than one apply→verify cycle: flaky verification, state drift, iterative fixes, bringing a runtime/DB/projection to a desired state.
- Use the limits from `classify-task` (Light 1 attempt, Standard 2, Heavy owner-defined).

## Loop

1. **Desired state** — state it concretely and observably (what "converged" means).
2. **Observe** — read the actual current state (DB outcome, file, projection, log) — not your assumption of it.
3. **Diff** — what differs from desired.
4. **Plan** — the minimal change to close the diff, inside Scope and allowed paths.
5. **Apply** — make that change only.
6. **Verify** — gather evidence at the required Evidence Level (`verify-completion`).
7. **Converged?** → yes: done. → no: check stop conditions before looping.

Use the `docs/templates/operation-controller.md` template to record desired/observed/diff/plan/attempts/changed-files/stop-condition.

## Stop conditions (any one → stop, rollback, or escalate)

- max attempts reached (per Weight Class)
- max changed files exceeded
- forbidden scope / protected path / protected capability touched (`docs/specs/{change,capability}-boundary.md`)
- rollback trigger reached
- verification cannot observe the outcome (no evidence path)
- owner-only decision surfaced
- observed state diverges from the task contract
- **verification fails twice for different reasons** → stop and report observed state, diff, and suspected root cause

## Rules

- Do NOT continue reconciling by expanding scope. Widening the diff to make verification pass is the failure mode this loop exists to prevent (§15.4, §22.4).
- Each attempt is observe→apply→verify, not apply→apply→apply. Re-observe before each new plan.
- On stop: report, don't silently push past the limit. Hand scope/risk events to `control-change`; hand stop/rollback decisions to `log-decision`.

## Output

- Desired vs observed vs diff
- Attempts used / changed files
- Verification result + Evidence Level
- Outcome: converged | stopped (reason) | rolled back | escalated
