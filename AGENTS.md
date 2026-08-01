# AGENTS.md

AI コーディングエージェント（Claude Code / Codex / GitHub Copilot 等）共通の作業ガイド。
Codex は作業前にこのファイルを読むため、ここには repo 共通方針のみ記す。
ツール固有の指示は各ツールのファイル（例: Claude Code は `CLAUDE.md`）に置く。

## プロジェクト概要

- 目的: 同一の California Housing + 同一SHAの学習コードを5つのマネージドML基盤（Vertex AI / SageMaker / Azure ML / Databricks / Snowflake）で実行し、選定に効く差分（IAM試行回数・IaC境界・Neon到達経路・残留リソース・課金構造）を実測して文書化する。主成果物は `docs/comparison/` の比較レポート＋選定チェックリスト。
- 主要技術: Python 3.12 / LightGBM / FastAPI / Terraform / Neon PostgreSQL。詳細は `docs/01_requirements.md` と `docs/02_architecture.md`。
- 状態（2026-08-01）: **5基盤すべて実測完了**（Phase 1〜5 とも完了条件 8/8）。RMSE `0.4368055090296257` と 1件推論の予測値 `4.183217948107466` は5基盤で一致し、差が出たのは到達までの経路のみ。結論は `docs/comparison/selection-checklist.md`（主成果物）、残留は `docs/comparison/residual-resources.md`、基盤別の一次記録は `docs/comparison/01`〜`05`。
- **実測値は書き換えない。** 各基盤ページは「予想 → 実測 → 差分」で書かれており、外れた予想こそが成果物。後から予想を実測に合わせて直さない。再実行して値が変わったら追記して並べる。
- 未消化の課題は `docs/tasks/02_backlog/` に残っているものだけ（消化済みは `05_done/` へ移動済み）。流用元と移植時の改変は各 adapter のモジュール docstring に記録済み。

## セットアップ / 主要コマンド

```bash
make setup    # 依存取得 + ビルド
make dev      # 開発サーバー
make test     # テスト
make fmt      # フォーマット
```

## コーディング規約

- 既存のコード・命名・パターンに合わせる。新規導入より既存の再利用を優先する。
- 変更後はテストとフォーマッタを実行してから完了とする。
- 非機密の設定値は `env/config.yaml`、ローカル秘密情報は `env/secret.yaml`、チーム共有・本番クレデンシャルは Doppler (`doppler.yaml`)。秘密情報をコミットしない。

## ドキュメント

設計・仕様・運用は `docs/` 配下を参照。更新規約と権威順位は `docs/00_index.md` に従う。
仕様レベルの変更は連動するドキュメントを同一 PR でまとめて直す。

## Codex / Claude Code

- `AGENTS.md` は Codex / 他エージェント共通ガイド。
- `CLAUDE.md` は Claude Code の司令塔。
- `.claude/rules/` と `.claude/skills/` は Claude Code 用。Codex が読む前提にしない。
- Codex 向けに永続させたい recurring な指摘やミス防止は、この `AGENTS.md` または nested `AGENTS.md` に小さく追加する。

## Harness（AI 制御一式）

- このリポジトリの AI 制御の全体像は `.claude/README.md`（Kurosawa Thin Harness Architecture の実装）。
- アーキ本体（tool-agnostic マスター）は `docs/specs/kurosawa-thin-harness-architecture.md`、repo 固有の脅威モデルは `docs/specs/{capability-boundary,change-boundary,runtime-protocol,evidence-policy,judgment-memory}.md`。
- permissions の ask/deny と保護パスは脅威モデルで決める。**他プロジェクトの設定をそのまま移植しない**。

## Task / Skill

- 一回性の作業計画・調査メモ・実装タスクは `docs/tasks/` に置く。
- Claude Code で繰り返し使う作業手順は `.claude/skills/` に置く（classify-task → create-task → scan-decisions → plan-skeleton → execute-task → verify-completion → review-task のライフサイクル）。
- Codex repo skills を本格運用する場合は `.agents/skills/` を任意追加する。標準生成物には含めない。
- task note を仕様の正本にしない。確定した仕様は `docs/specs/`、判断理由は `docs/decisions/`、運用手順は `docs/runbooks/` に昇格する。
