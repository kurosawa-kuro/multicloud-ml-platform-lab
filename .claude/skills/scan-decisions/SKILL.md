---
name: scan-decisions
description: Scan the task, docs, and code for points that need human judgment, batch the real blockers into one upfront question, default the rest, so execution runs without mid-task blocking
---

# Scan Decisions

Implements **Layer 3: Human Judgment Gate** of `docs/specs/kurosawa-thin-harness-architecture.md` (§11). Before executing, find every point that needs a human decision, ask the real blockers **once** upfront, and pick safe defaults for the rest — so the agent can do the bulk of the work without stopping to confirm midway. The gate exists to stop the agent from processing an owner-only *value* decision while disguising it as an *implementation* decision (§5.2).

The AI-facing stop rules (§11.4) — when to stop and ask owner — are listed in `docs/specs/runtime-protocol.md`; this skill is the upfront batching procedure.

## When to use
- After `create-task`, before `execute-task`, on any non-trivial work.

## Steps

1. Read the task and its linked `docs/specs/`, ADRs; inspect the code paths it touches. Combine with `check-claims` to confirm facts first.
2. Scan for decision points across these categories:
   - Ambiguous goal or scope (no measurable target / undefined "done").
   - Source-of-truth conflict (docs vs code, or two docs disagree). Do not guess which is canonical.
   - Irreversible or external-effect actions (deploy, secret push, data/schema delete, sending notifications). Cross-check `.claude/settings.json` ask/deny.
   - Multiple valid approaches with real trade-offs (library, pattern, naming).
   - Missing inputs / unstated assumptions (which account/DB, date range, env, credentials).
   - Owner-only preference calls (priority, keep-vs-delete, scope cuts).
3. Triage each point:
   - **BLOCKER** — a wrong guess is costly or irreversible, no sensible default exists, or only the owner can decide.
   - **DEFAULTABLE** — a reasonable, cheap-to-reverse default exists.
4. Batch **all** blockers into ONE question set and ask upfront (use AskUserQuestion). Do not drip-feed questions during execution.
5. For each DEFAULTABLE: pick the default, state it explicitly with a one-line reason and how to reverse it, then proceed.
6. Record results in the task note (Open Questions / Assumed Defaults) so they are durable and reviewable.
7. For each default taken on an ambiguous Goal/Scope (a real bet, not a trivial choice), also append one `default-taken` entry via `log-decision` — the task note holds the per-task detail, the decision log holds the cross-task behavioral record.

## Rules

- Front-load, don't over-ask: only true blockers go in the question; default everything else.
- Never silently guess a source-of-truth conflict or an irreversible choice — those are always blockers.
- One batched question beats many mid-task interruptions.
- A default is not a decision hidden from the human: always surface it.

## Output

- Blockers → one consolidated question (asked upfront)
- Assumed defaults (value + why + how to reverse)
- Risks introduced + rollback / change trigger
- Decisions appended to the task note
- Then: proceed with execution
