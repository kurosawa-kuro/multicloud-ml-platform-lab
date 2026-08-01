---
name: review-project
description: Review project structure, docs, responsibility boundaries, and missing tests
---

# Review Project

Review the project before proposing structural changes.

## Steps

1. Read `README.md`, `CLAUDE.md`, `AGENTS.md`, `docs/00_index.md`, and relevant specs first.
2. Inspect repository structure with `rg --files`, then identify main entrypoints.
3. Map modules to responsibilities: domain, application/usecase, ports, adapters, infrastructure, UI, DB, external I/O.
4. Report findings first, ordered by severity, with file paths and concrete evidence.
5. Separate confirmed facts from hypotheses. Do not invent modules, commands, or runtime behavior.

## Output

- Findings
- Responsibility map
- Boundary problems
- Test or docs gaps
- Recommended next actions
