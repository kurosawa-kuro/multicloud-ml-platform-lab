---
name: log-decision
description: Append one judgment call to the cross-task decision log (trade journal) so the agent's decision behavior is reviewable over time, separate from per-task notes and architectural ADRs
---

# Log Decision

Implements the **Decision Log** entry point of **Layer 9: Judgment Memory** (`docs/specs/kurosawa-thin-harness-architecture.md` §17). This captures raw judgment events; `distill-memory` later promotes the recurring/high-loss ones into reusable principles. The full pipeline and its relation to the global `~/.claude` memory are in `docs/specs/judgment-memory.md`.

Append a single judgment call to `docs/decisions/decision-log.md` — the append-only **trade journal** of how decisions were actually made, across tasks. This is the reviewable record of the agent's *betting behavior*: which thesis, what blast radius, what stop-loss, what outcome. It is not a spec (`docs/adr/`) nor per-task working memory (`docs/tasks/`).

## When to use

Log when a real judgment call is made — the moments a discretionary trader would write in a journal. Typically from inside `scan-decisions`, `control-change`, `assess-risk`, or `verify-completion`:

- **default-taken** — an ambiguous Goal/Scope was resolved by picking a default instead of asking (`scan-decisions`).
- **scope-cut** — out-of-scope work was deferred / rejected / dropped (`control-change`).
- **approach-choice** — chose one approach over a real alternative with trade-offs (library, pattern, store, naming).
- **rollback** — a stop/revert trigger fired; work was backed out or restarted.
- **risk-accepted** — proceeded knowing a failure mode exists (the duplicate/drift/silent-failure kind `assess-risk` surfaces).
- **approval-deferred** — an irreversible/external-effect/owner-only call was stopped and handed to the user.

Do **not** log routine mechanical work, reads, or tool calls with no judgment. A session full of those should leave the log untouched (the Stop hook will not add a boundary either).

## Steps

1. Decide it clears the bar above (a real call, not a chore). If not, skip — silence is a valid log.
2. Append **one** block to the end of `docs/decisions/decision-log.md`, never editing earlier entries.
3. Fill every field; map them to the trading lens:
   - `根拠 (why)` = entry thesis — why this call.
   - `影響範囲 (blast radius)` = lot/leverage — what could break, how reversible.
   - `撤退条件 (stop/revert)` = stop-loss — what aborts it and how to back out.
   - `結果 (outcome)` = `open` if not yet known; come back and update only this line when it resolves.
4. Keep it to a few lines. The log is scannable, not an essay.

## Template

```markdown
## <UTC timestamp> — <one-line decision>
- type: default-taken | scope-cut | approach-choice | rollback | risk-accepted | approval-deferred
- 根拠 (why): なぜこの判断か（建玉理由 / entry thesis）
- 影響範囲 (blast radius): 何が壊れうるか・可逆性（レバ/ロット）
- 撤退条件 (stop/revert): 何が起きたら戻す・どう戻す（損切りライン）
- 結果 (outcome): win | loss | open | 塩漬け
- link: task note / ADR / PR（任意）
```

## Rules

- Append-only. Past entries are never rewritten; only an entry's `結果` line may be updated when the outcome lands.
- No secrets, tokens, cookies, private paths, or real personal/operational data (`.claude/rules/security.md`). Record the *structure* of the decision, not payloads.
- One entry per judgment call. Do not batch a whole session into one vague line.
- This does not replace the task note. Durable per-task reasoning still goes in `docs/tasks/`; promote stable architecture to `docs/adr/`. The log is the cross-task behavioral record only.
- Session boundaries are inserted by the Stop hook, not by hand.
