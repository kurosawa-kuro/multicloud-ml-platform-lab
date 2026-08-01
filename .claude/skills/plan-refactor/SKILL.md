---
name: plan-refactor
description: Plan refactors for duplicated logic and responsibility drift without premature abstraction
---

# Plan Refactor

Plan a refactor for scattered similar logic while preserving current behavior.

## Review Points

- Inventory duplicated or similar processing.
- Judge what should be shared and what should remain separate.
- Split modules that violate single responsibility.
- Separate domain logic from external I/O, DB, framework, UI, and operational concerns.
- Clarify port / adapter / repository boundaries where applicable.
- Add tests that preserve existing behavior before structural changes.
- Reduce abstractions that no longer pay for themselves.

## Rules

- Do not commonize code only because it looks similar.
- Do not create vague shared utility dumping grounds.
- Do not treat design patterns as the goal.
- Do not change structure before confirming current behavior.
- Do not propose large changes without a test strategy.

## Output

- Duplication inventory
- Commonize / keep separate decisions
- Boundary redesign proposal
- Test plan
- Phased refactoring plan
- Deletion or isolation candidates
