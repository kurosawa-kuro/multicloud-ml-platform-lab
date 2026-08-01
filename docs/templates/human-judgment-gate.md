# Human Judgment Gate

Template for **Layer 3** (`scan-decisions`). See `docs/specs/kurosawa-thin-harness-architecture.md` §11.6 and the repo stop rules in `docs/specs/runtime-protocol.md`.

## Is there any owner-only decision?

- [ ] Value / priority decision
- [ ] Scope change
- [ ] Safety boundary change
- [ ] Production / DB / Secret / Cloud operation
- [ ] Rollback-difficult change
- [ ] Domain meaning change
- [ ] Cost / contract / permission impact
- [ ] Ambiguous requirement with high impact
- [ ] Existing bug found outside current scope

## Decision

- proceed | ask-owner | defer | split-task | stop

## Reason

## Follow-up
