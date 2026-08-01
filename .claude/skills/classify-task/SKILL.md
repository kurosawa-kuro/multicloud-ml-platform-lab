---
name: classify-task
description: Classify a task as Light / Standard / Heavy (Harness Weight Class) and set its default limits, so the rest of the harness applies only as much control as the task's risk and reversibility justify
---

# Classify Task

Implements **Layer 1: Harness Weight Class** of `docs/specs/kurosawa-thin-harness-architecture.md` (§9). Pick the smallest weight class the task safely fits, then apply only the controls that class requires. This is the lever that keeps the harness thin: Light tasks must NOT pay Standard/Heavy ceremony.

## When to use

- Right after `create-task`, or the moment you start any non-trivial work, before planning or editing.
- Re-run it if scope grows mid-task (a Light task that starts touching protected paths becomes Heavy — that itself is a `control-change` event).

## Classes

**Light** — docs edits, small adapter fixes, clear scope, easy rollback. Does not touch production / DB / Secret / IAM / Cloud, does not change domain meaning.
- Requires: Goal / Scope / Done / Evidence only.
- Limits: max attempts 1 · max changed files 3 · protected path changes 0 · protected capability changes 0.

**Standard** — usecase change, test additions, multi-file change, some spec interpretation. No production impact, reversible.
- Requires: Task Contract Lite + `scan-decisions` (Human Judgment Gate) + `control-change` (Change Boundary) + Evidence Level ≥2.
- Limits: max attempts 2 · max changed files 8 · protected path changes 0 · protected capability changes 0.

**Heavy / Protected** — domain meaning change, production / DB / Secret / IAM / Cloud, migration, billing/quota, hard-to-rollback, high-impact ambiguity.
- Requires: Task Contract Full + Owner Approval + Capability Boundary + Protected Paths + Rollback Plan + Reconcile Limits + Evidence Level 4 + `log-decision`.
- Limits: owner-defined; protected path / capability changes require owner approval.

See `docs/specs/capability-boundary.md` and `docs/specs/change-boundary.md` for what counts as protected **in this repo**.

## Steps

1. Check the hard triggers first. If the task touches any protected capability or protected path (production deploy, secret push, production/master data write, schema/migration, paid API cost, external notification send, `infra/**`, `env/secret/**`, generated config) → it is **Heavy** regardless of how small the diff looks.
2. Otherwise, if it changes behavior / usecase / multiple files / tests → **Standard**.
3. Otherwise (docs, single small reversible local edit) → **Light**.
4. Record the class, the reason, and the default limits in the task note (use the `docs/templates/harness-weight-class.md` template).

## Rules

- When in doubt between two classes, pick the heavier one only if a protected boundary is in play; otherwise prefer the lighter class to keep the harness thin (§22.2 — an over-strong gate kills speed).
- Light tasks skip `scan-decisions` batching, `plan-skeleton`, and `reconcile-task` unless something forces an upgrade.
- A mid-task upgrade (Light→Standard→Heavy) is a scope/risk event: route it through `control-change`.

## Output

- Class: Light | Standard | Heavy
- Reason (1 line)
- Default limits (max attempts / max changed files / protected path / protected capability)
- Owner approval required? yes | no
