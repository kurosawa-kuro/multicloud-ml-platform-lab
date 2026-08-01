---
name: harness-trim
description: Run once right after generating a project (and whenever the threat model shifts) to right-size the deliberately-heavy harness — decide keep/delete/adjust for each hook, rule, agent, skill, and permission line against what THIS project actually needs to protect, then record the calls. The generator ships heavy on purpose; this is the intended trimming step.
---

# Harness Trim

The project generator ships the harness **deliberately heavy** (every hook, rule, agent, skill, and a broad permissions menu), because richness flows template → generated project and it is cheaper to delete than to remember to add. This skill is the other half of that contract: the **trimming pass** that right-sizes the shipped harness to this project's real threat model. Run it once, early, so the project does not carry controls it will never use — and so the ones it keeps are actually tuned.

## When to use

- **Once, right after generation**, before the harness calcifies. Its initial state is "heavy default", not "final".
- Again whenever the threat model shifts (starts touching production / secrets / a new cloud / paid APIs, or the opposite — becomes a throwaway with nothing loss-critical).

## Method — name the threat model first, then cut to it

1. **Write down what THIS project must protect.** Pick from: secrets/credentials · irreversible or external side effects · source-of-truth data integrity · production/paid systems. If none apply (throwaway, nothing loss-critical), you will trim aggressively. If several apply, you will keep and thicken.
2. **Walk each harness surface and mark keep / delete / adjust:**
   - `.claude/settings.json` permissions — delete allow/ask/deny lines for toolchains this project does not use (e.g. no Terraform → drop `terraform*`, `infra/**`; no cloud → drop `gcloud`/`aws`/`bq`). Tighten to ask/deny where a real loss-critical asset exists; loosen raw tools to allow only where "breaks are cheaply rebuildable".
   - `.claude/hooks/*` — keep the ones whose boundary is real here. No secrets in play → `detect-secret-*` can go. Don't want auto-format → delete `format-on-edit.sh` and its settings entry. Each hook is one deletable file + one settings block.
   - `.claude/rules/*` — delete rules for languages/areas not present (no Python → delete `python.md`). They are `paths`-scoped so cheap to keep, but delete dead ones to reduce noise.
   - `.claude/agents/*` — keep the review/verify agents you will actually invoke.
   - `.claude/skills/*` — keep the harness lifecycle you will use. A genuinely Light-only project may drop the Heavy-only ceremony skills.
   - `docs/specs/*` and `docs/templates/*` — the threat-model instantiations (`capability-boundary.md`, `change-boundary.md`) must be rewritten to this project, not left generic. `detect-safety-boundary.sh` protected paths must match `change-boundary.md`.
3. **Adjust, don't just delete.** Set the real protected paths in `detect-safety-boundary.sh`, the real weight-class triggers in `capability-boundary.md`, the real formatters if the defaults are wrong.
4. **Verify the harness still loads.** After edits: the JSON in `settings.json` parses (`jq . .claude/settings.json`), referenced hook files exist, `bash -n` each kept hook.
5. **Record the calls.** One line in `docs/decisions/decision-log.md` per non-trivial keep/delete with the reason (so a future you does not re-litigate why `terraform` permissions are gone). This IS a `log-decision` moment.

## Rules

- Deleting a shipped control is a normal, expected move here — not scope creep. The heavy default assumes you will cut.
- But cut against the *named* threat model, not by taste. If you cannot say what a control protects, that is the argument for deleting it — write that down.
- Never delete a hard control (secret gate, protected-path block) while the asset it guards is still in play. Downgrade deliberately, with a decision-log line.
- If you find yourself keeping everything and every task feels Heavy, that is the §22 failure mode — trim harder.
