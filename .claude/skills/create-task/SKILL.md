---
name: create-task
description: Create a durable task note under docs/tasks without turning it into a spec, using the Task Contract (Lite for Light/Standard, Full for Heavy) sized to the task's weight class
---

# Create Task

Create one focused task file under `docs/tasks/`. This is **Layer 2: Task Contract** of `docs/specs/kurosawa-thin-harness-architecture.md` (§10): fix the task's outer boundary before execution so scope cannot drift silently.

**SPEC.md との境界**: `SPEC.md`（root）は「いま実装する1機能」の深い実装スペック（触るファイル/IF 名指し＋末尾 E2E 検証、使い捨て・公式入口）。この task note は多数の作業を追う**台帳**（軽量・恒久、`02_backlog`/`03_active`/`04_verifying`/`05_done`）。Standard/Heavy 機能で SPEC.md を書く場合、Goal/Scope を両方に重複させず、task note は `SPEC.md` へリンクして「進行・evidence・優先度」だけ持つ。

## ライフサイクル（番号付き5状態フォルダ）

task note は番号付きの5状態ライフサイクルで管理する。**番号が状態の順序と物理 mv の方向を表す**（task file は必ず番号が**上がる**向きにだけ移動する）。

- `docs/tasks/01_idea/` — まだ task 化に値しない生アイデア。
- `docs/tasks/02_backlog/` — 形にはなったが未着手。**owner 判断待ち**の作業もここで待つ（着手前に owner の判断が要るもの）。
- `docs/tasks/03_active/` — 進行中、または次に実行するもの（手を動かす段）。
- `docs/tasks/04_verifying/` — 実装と基本検証は完了したが、**terminal state**（監視 probe の緑化など）が未観測で done にできない**収束待ち**。active と done の間の状態。
- `docs/tasks/05_done/` — 完了（terminal state を観測済み）。

Pick the contract by weight class (`classify-task`):
- **Light / Standard → Task Contract Lite** (the default below).
- **Heavy / Protected → Task Contract Full** — only when production / DB / Secret / IAM / Cloud, domain meaning, migration, or high-impact ambiguity is in play.

Full templates: `docs/templates/task-contract-lite.md`, `docs/templates/task-contract-full.md`.

## Rules

- 既定は `docs/tasks/02_backlog/`。user が明確に active と言った場合だけ `docs/tasks/03_active/` に置く。
- 進行中・次に実行するものは `docs/tasks/03_active/`。
- 完了（terminal state を観測済み）のノートだけ `docs/tasks/05_done/`。
- 状態が進んだら **task file を物理的に次の番号のフォルダへ mv** する。ファイル内の status 行で状態を持たない。
- Do not duplicate source-of-truth behavior. Link to `docs/specs/`, `docs/adr/`, or `docs/runbooks/` instead.
- Name task files as `YYYY-MM-DD-topic.md`.
- In `Value`, pick 1-2 from: safety / notification quality / collection accuracy / integrity / dev speed / cost / failure detection / docs canonicalization. Do not write an essay; skip trivial-value work.
- Do not include secrets, private paths, credentials, logs, or personal operational data.
- Do not pay Full-contract ceremony on a Light/Standard task — that breaks Thin Harness (§22.2).

## 04_verifying（収束待ち）の入退室ルール

`04_verifying` は「レビュー待ち」でも「判断待ち」でもない。**実装・基本検証は済んだが、反証可能で外部から観測できる terminal signal がまだ緑になっていない**ときだけ使う。

- **入室条件**: コード＋基本検証が完了し、残るは「タイミングを自分で制御できない terminal signal を観測するだけ」の状態。多くは**既存の監視 probe / health check の緑化**、定期実行の完走、下流 surface への反映など。
- **`05_done/` への退室**: その terminal signal を実際に緑と観測できた時だけ（Evidence Level ≥2）。どの probe / コマンド / surface で何を観測したか（緑の実測結果）を task に記録し、file を `05_done/` へ物理 mv する。
- **判断待ちを混ぜない**: 「owner の判断待ち」は verifying ではなく `02_backlog/` へ。verifying が待つのは *evidence* であって *判断* ではない。
- signal が赤・古い場合は `03_active/` へ戻して直す。happy-state や陳腐化した signal で done にしない。

## Task Contract Lite (Light / Standard)

```markdown
# Task Title

## Goal

## Value

## Context

## Scope

## Non-scope

## Plan

## Acceptance Criteria

## Stop / Ask Owner If
```

## Task Contract Full (Heavy / Protected)

Use `docs/templates/task-contract-full.md`. Beyond Lite it adds: Risk · Owner-only Decisions · Capability Boundary · Allowed Paths · Forbidden Paths · Rollback Trigger · Evidence Required (Level 4). Heavy tasks also require Owner Approval before execution.
