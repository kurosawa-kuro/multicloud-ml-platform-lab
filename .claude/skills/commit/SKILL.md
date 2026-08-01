---
name: commit
description: Create a clean, verified git commit — gate on evidence first (check + verify-completion), stage deliberately, write a message that says why, and never commit secrets or on red. Solo repos commit to the working branch directly; push stays a separate owner-gated step.
---

# Commit

Turn verified work into one coherent commit. The commit is a claim that this change is done and safe — so the gate runs *before* the commit, not after.

## Preconditions (do not commit until all hold)

1. **Green gate** — `check` was run this session and passed (fmt/lint/test). Never commit on red.
2. **Evidence** — the Goal is met at the required Evidence Level (`verify-completion`, ≥2; production = 4). A commit is not evidence.
3. **No secrets** — `git diff --staged` contains no credentials, tokens, `.env*`, `env/secret*.yaml`, tfstate/tfvars, real project IDs, webhooks, or personal absolute paths. If a secret was ever staged, unstage and rotate it.
4. **Docs in step** — behavior/CLI/API/config changes carry their docs and tests in the same commit (no drift).

## Steps

1. Review the full diff: `git diff` and `git diff --staged`. Understand every hunk; drop stray "while I'm here" edits (scope creep → separate commit or revert).
2. Stage deliberately (`git add <paths>`), not `git add -A` blindly. Confirm with `git status`.
3. Write the message: a concise subject in the imperative, then a body that says **why** the change was made and any non-obvious trade-off — not a restatement of the diff.
4. Commit. Do not amend or rebase already-pushed history.

## Rules

- **Branch policy is project-defined.** Solo/personal repos commit to the working branch directly (no branch-per-change ceremony). Shared repos branch first — follow the repo's stated convention; when unstated on a shared repo, branch.
- `commit` stops at the commit. `git push` is a separate, owner-gated step (ask in settings.json) — do not push unless explicitly asked.
- Commit at meaningful boundaries: one logical change per commit, gate green each time. Don't batch unrelated changes.
