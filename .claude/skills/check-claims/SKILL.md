---
name: check-claims
description: Verify claims against repository files and commands before writing conclusions
---

# Check Claims

Verify claims against the actual repository before writing conclusions or plans.

## Steps

1. Use `rg --files` to confirm files and directories exist.
2. Use `rg` to confirm symbols, functions, routes, tables, config keys, Makefile targets, commands, test names, and documentation references.
3. Quote or cite file paths for every important claim.
4. Mark anything not found as "not confirmed" instead of guessing.
5. If a claim depends on runtime behavior, identify the command or test needed to verify it.

## Output

- Confirmed facts with paths
- Not confirmed / missing evidence
- Commands run
- Follow-up verification needed
