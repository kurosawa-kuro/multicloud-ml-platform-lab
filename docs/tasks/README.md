# タスク

このディレクトリは、毎日の作業計画・調査・実装チェックリスト・完了証跡の実行ハブ。プロダクトの挙動・アーキテクチャ・データモデル・運用 runbook の正本ではないが、「今日何をするか」「次に何をするか」「何を完了したか」はここを正とする。

確定した仕様は `docs/specs/`、判断は `docs/decisions/`、繰り返す運用は `docs/runbooks/`、再利用する作業手順は `.claude/skills/` に置く。

## 日次運用

1. 作業開始時に `03_active/`（と収束待ちの `04_verifying/`）を見る。
2. 今日やることを 1 つ選び、必要なら task file を作る。
3. 実装前に Scope / Plan / Acceptance Criteria を更新する。
4. 作業中の判断、検証コマンド、未解決事項を task file に残す。
5. 実装・基本検証が済んだら `04_verifying/` へ mv し、terminal signal（監視 probe 緑化など）を待つ。
6. signal を緑と観測できたら証跡を追記し、`05_done/` へ mv する。
7. 仕様として残すべき内容は `docs/specs/` や 01〜08 文書へ昇格する。
8. 判断理由として残すべき内容は `docs/decisions/` へ昇格する。

`tasks/` は軽く保つが、軽すぎて運用履歴が消えるのは避ける。日々の作業で迷ったら、まず task に書いてから実装へ進む。

## 構成（番号付き5状態ライフサイクル）

タスクは番号付きの5状態フォルダで管理する。**番号が状態の順序と物理 mv の方向を表す**（task file は必ず番号が**上がる**向きにだけ移動し、状態を file 内の status 行で持たない）。

```text
01_idea → 02_backlog → 03_active → 04_verifying → 05_done
```

| ディレクトリ | 状態 | 用途 |
|---|---|---|
| `01_idea/` | 生アイデア | まだ task 化に値しない未整理の思いつき |
| `02_backlog/` | 未着手 | 形にはなったが未着手。**owner 判断待ち**もここで待つ（着手前に判断が要るもの） |
| `03_active/` | 進行中 | 進行中、または次に実行するもの（手を動かす段） |
| `04_verifying/` | 収束待ち | 実装・基本検証は完了したが、terminal state（監視 probe 緑化など）が未観測で done にできない状態 |
| `05_done/` | 完了 | terminal state を観測して完了。必要なら判断を `docs/decisions/` へ昇格する |

**5つとも実在させる**（2026-08-02 整理）。以前は `01_active/` という規約外のフォルダがあり、
README の記述（5状態）と実在（3状態）がずれていた。空の状態フォルダは git が追跡しないため
`.gitkeep` を置いてある。**空でも消さないこと** —— 消すと同じずれが再発する。

### 04_verifying（収束待ち）の入退室ルール

`04_verifying` は「レビュー待ち」でも「判断待ち」でもない。**実装・基本検証は済んだが、反証可能で外部から観測できる terminal signal がまだ緑になっていない**ときだけ使う。

- **入室**: コード＋基本検証が完了し、残りは「タイミングを自分で制御できない terminal signal を観測するだけ」の状態（既存の監視 probe / health check の緑化、定期実行の完走、下流 surface への反映など）。
- **退室（→ `05_done/`）**: その signal を実際に緑と観測できた時だけ（Evidence Level ≥2）。どの probe / コマンド / surface で何を観測したかを task に記録し、file を `05_done/` へ物理 mv する。
- **判断待ちを混ぜない**: 「owner の判断待ち」は verifying ではなく `02_backlog/` へ。verifying が待つのは *evidence* であって *判断* ではない。
- signal が赤・古い場合は `03_active/` へ戻す。happy-state や陳腐化した signal で done にしない。

## タスクの粒度

- 1 task は、1 つの目的と 1 つの完了条件を持つ。
- 1 日で終わらない task は、今日やるチェック項目を `Plan` に切り出す。
- 大きすぎる task は `02_backlog/` に分割案を置き、実行単位だけ `03_active/` に出す。
- 仕様変更、設計判断、運用手順が固まったら、task に閉じ込めず docs 本体へ昇格する。

## 関連スキル

| スキル | 用途 |
|---|---|
| `.claude/skills/create-task` | ユーザー依頼からタスクノートを作る |
| `.claude/skills/execute-task` | 挙動とテストを保ったままタスクを実行する |
| `.claude/skills/review-task` | 範囲・根拠・クローズ可否の観点でタスクをレビューする |
| `.claude/skills/review-project` | プロジェクト構成と責務境界をレビューする |
| `.claude/skills/plan-refactor` | 早すぎる共通化を避けてリファクタを計画する |
| `.claude/skills/check-claims` | 結論前に主張をファイルとコマンドで検証する |
| `.claude/skills/plan-skeleton` | テスト・実装の前にビジネスロジックレスのスケルトンで構造を固定する |

## Active

| ファイル | 用途 |
|---|---|
| [03_active/refactoring-candidates.md](03_active/refactoring-candidates.md) | 残りのクリーンアップ候補 |

## ルール

ここにタスク詳細を重複させない。各タスクファイルは軽く保つ:

```markdown
# タスクタイトル

## Goal

## Context

## Scope

## Skeleton

## Plan

## Acceptance Criteria

## Verification

## Notes
```

次に着手できる作業は
[03_active/refactoring-candidates.md](03_active/refactoring-candidates.md) で管理する。
