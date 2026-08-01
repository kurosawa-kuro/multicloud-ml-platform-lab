---
name: distill-memory
description: Periodically distill the raw decision log into reusable judgment principles — promote recurring/high-loss decisions to distilled memory, reject one-off or context-bound ones — so the journal does not bloat into an unread log
---

# Distill Memory

Implements **Layer 9: Judgment Memory** distillation of `docs/specs/kurosawa-thin-harness-architecture.md` (§17). `log-decision` collects raw decision events; this skill is the review step that turns the recurring, high-loss ones into principles the agent can actually reuse. Repo-specific policy and the relationship to the global `~/.claude` memory are in `docs/specs/judgment-memory.md`.

## When to use

- Periodically (e.g. when the decision log accumulates several session boundaries), or after an incident, or when asked to review the agent's decision behavior.
- Not every session. This is observation→distillation, never an always-on runtime rule (§17.2).

## Lifecycle

```
Decision Event (docs/decisions/decision-log.md, raw)
  → Memory Candidate (docs/memory/memory-candidates.md)
    → Distilled Memory (docs/memory/distilled-memory.md)   [reusable principle]
      → Runtime Rule / Hook / Skill                         [only if mechanically detectable + high loss]
  → Rejected / Expired (docs/memory/rejected-memory.md)
```

## Steps

1. Read `docs/decisions/decision-log.md` since the last distillation. Group entries by recurring pattern (same kind of bet, same scope-creep, same stop-loss miss).
2. For each pattern, decide:
   - **Promote to candidate** if it recurred 2–3×, or one occurrence was high-loss, or it touched a protected capability/path, or an owner-only line was crossed.
   - **Reject / expire** if it was one-off, strongly context-bound, would only dull the agent's exploration, or is already caught by tests / CI / permissions.
3. Move qualifying candidates to `distilled-memory.md` as a short, abstract, reusable principle (the *lesson*, not the incident).
4. Propose promotion to a `rule` / `hook` / `skill` **only** when the trigger is mechanically detectable AND the loss is high AND an always-on rule would be cheap to run (§8.3, §17.5). Otherwise leave it as distilled memory.
5. Record rejections in `rejected-memory.md` with the reason — a rejected lesson is recorded, never silently dropped.

## Rules

- Distilled memory is the only memory layer the agent reads at runtime; the raw log is for review, not for loading every session (§17.2, §22.8).
- Keep principles abstract and few. A distilled memory that reads like a task log has failed.
- No secrets, tokens, or personal/operational payloads — record the *structure* of the judgment (`.claude/rules/security.md`).
- Repo-local distilled memory ≠ global `~/.claude` memory: this file holds this repo's judgment principles; cross-project facts belong in the global store. See `docs/specs/judgment-memory.md`.

## Output

- New / updated distilled principles
- Candidates still pending more evidence
- Rejected / expired (with reason)
- Any rule/hook/skill promotion proposals (with the mechanical trigger)
