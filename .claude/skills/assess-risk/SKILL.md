---
name: assess-risk
description: Before or during risky work, assess what breaks if it fails — beyond what permissions already block
---

# Assess Risk

Supports **Layer 4: Capability Boundary** and Risk Management of `docs/specs/kurosawa-thin-harness-architecture.md` (§12). The repo's protected capabilities (and why they are gated) are in `docs/specs/capability-boundary.md`.

`scan-decisions` asks "does a human need to decide?". This asks "if this fails, what breaks?". Permissions stop dangerous commands; they do not cover silent data or quality failures. Use this for risky changes.

## When to use
- Before risky or irreversible work: secret / deploy / DB apply / schema change / cleanup apply / paid API cost / external notification / worker / scheduler / source-of-truth change.
- Skip for low-risk work: typo / docs wording / test rename / dry-run-only investigation.

## Steps

1. List the blast radius across this project's failure modes:
   - secret exposure / external side-effect (deploy, notification, secret push).
   - data integrity: duplicates, source-of-truth drift across stores, retention.
   - quality: degraded output, noisy / duplicate side-effects.
   - operations: silent service / worker / queue failure, resource bloat, cost runaway.
2. For each real risk: state likelihood, impact, and how it would be detected (monitoring / persisted outcome / log).
3. State the rollback or mitigation, and whether a dry-run / bounded run exists.
4. If a risk has no detection or no rollback, raise it as a blocker via `scan-decisions`.

## Rules

- Permissions are not a risk plan; they block commands, not bad outcomes.
- A risk with no detection path is a blocker, not an assumption.
- Prefer a dry-run / bounded run before the real run.

## Output

- Risk list: likelihood | impact | detection | rollback
- Blockers (no detection / no rollback) → hand to `scan-decisions`
- Go / no-go recommendation
