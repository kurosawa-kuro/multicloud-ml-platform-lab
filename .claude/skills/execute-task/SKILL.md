---
name: execute-task
description: Execute a docs/tasks item while preserving behavior, tests, and docs
---

# Execute Task

Implement the work described in a `docs/tasks/` file.

## Steps

1. Read the task file and linked docs before editing.
2. Confirm current behavior with tests, static checks, or targeted inspection.
3. Keep behavior changes aligned with `docs/specs/` and `docs/03_domain_model.md`.
4. Implement in small steps and update tests/docs for behavior, API, DB, CLI, UI, or operations changes.
5. Run relevant checks from `Makefile`.
6. Move or mark the task as done only after acceptance criteria are met.

## Safety

- Stay within the task's Scope. If you find out-of-scope changes, broken assumptions, or new risk, do not silently implement them — classify via `control-change` or split into a follow-up task.
- Do not expose secrets, private paths, tokens, generated reports, or raw operational data.
- Do not run destructive commands unless the user explicitly asks.
