# 08 リリース Runbook

> このプロジェクトの「リリース」= **1基盤分の Phase を完走し、比較レポートの1列を実測で埋めること**（常駐サービスのデプロイは存在しない。インフラは Phase ごとに使い捨て）。コマンドは設計段階（未実装）のものを含む — [04_workflows.md](./04_workflows.md) の注記に従う。

## リリース前（Phase 開始前）

```bash
make test
git status --short
```

- 該当 Phase の **precheck タスクを消化済み**であることを確認する（[02_backlog/](./tasks/02_backlog/) の `*-check.md` / `*-precheck.md`）。
- `docs/tasks/03_active/` と `04_verifying/` に blocker が残っていないことを確認する。
- 予算残を確認する（Tier A 各 ¥2,000/月・Tier B 各 ¥1,000/月・合計 ¥8,000/月）。

## デプロイ（Phase 実行）

[04_workflows.md](./04_workflows.md) の「フェーズ実行ワークフロー」を順に実行する（apply → train → collect → register → predict）。

## デプロイ後 smoke（Golden Path）

要件の Critical User Journey / Golden Path（[01_requirements.md](./01_requirements.md)）を、実クラウドで端から端まで実際に1回通す。個別ヘルスチェックが緑でも、この一本が切れていたらリリース失敗として扱う（＝ロールバック判定）。

| # | Golden Path ステップ | 確認方法（実行するコマンド / 操作） | 期待する観測結果 |
|---|---|---|---|
| 1 | terraform apply で基盤構築 | `make <cloud>-apply` 後、Neon で `select * from infra_events where action='apply' order by created_at desc limit 1` | 当該 platform の apply 行が所要・リソース数付きで存在する |
| 2 | 学習ジョブ成功 | `make <cloud>-train` 後、`select status, attempt, failure_class from ml_runs where stage='train' and platform='<p>' order by created_at` | success 行が存在し、失敗試行も failure_class 付きで全件残っている |
| 3 | Neon 到達・登録・1件推論 | `select write_path, metrics->>'rmse' from ml_runs where platform='<p>' and stage='train' and status='success'` + `make <cloud>-predict` | write_path が記録され、RMSE がローカル基準と一致し、推論レスポンスが返る |
| 4 | destroy → 残留記録 → レポート記述 | `make <cloud>-destroy && make check-residual` 後、`docs/comparison/` の当該基盤ページを開く | residual_resources が記録され、レポート1列が実測値で埋まっている |

- 中間段（apply 緑・ジョブ緑）だけで完了にしない。**レポートの1列が実測で埋まった状態を観測して初めて Phase 完了**とする（⑧は次 Phase のブロック条件）。

## ロールバック

- **トリガー**: smoke のいずれかが期待結果に到達しない場合、または月次コストが上限を超えた場合。
- **手順**: このプロジェクトのロールバック = **撤退**。`make <cloud>-destroy` → `make check-residual` で残留を記録し、残ったものは手動削除して再度 check-residual で確認する。**計測データ（Neon の ml_runs / infra_events）は消さない** — 失敗の記録こそ成果物。
- コスト超過時は Azure（Phase 4）を切り離す（[01_requirements.md](./01_requirements.md) 破綻条件）。
- Endpoint の消し忘れが最大の課金リスク。セッションを中断する場合も必ず destroy + check-residual を先に実行する。
- **Azure だけは契約状態も戻す対象**。2026-08-01 に無料試用版 → Pay-As-You-Go へアップグレードしており、`spendingLimit` による自動停止が効かない。Phase 4 完走後のリソース削除・quota 復旧・サブスクリプション取り消しの手順は [runbooks/動作検証-azureml.md §9](./runbooks/動作検証-azureml.md)（クレジット失効 2026-08-30 が実質のデッドライン）。

## リリース後タスク

- レポート1列の記述（⑧）が済んだら、Phase の task を `04_verifying/` → `05_done/` へ mv する（terminal state = レポート実測記入 + residual 記録）。
- 次 Phase の precheck を `02_backlog/` から確認する。
- 恒久的な運用手順になったものは `docs/runbooks/` へ昇格する。
