# 運用 runbook

繰り返し実行する手順の置き場。**仕様の正本ではない**（仕様は `docs/01`〜`08`、
日々の作業計画は `docs/tasks/`、判断は `docs/decisions/`）。

> **5基盤すべて完走（2026-08-01・各 8/8）。** 結論は
> [comparison/selection-checklist.md](../comparison/selection-checklist.md)。
> 以下の runbook は**再実行するときの手順**として維持する。

| runbook | Phase | 状態 |
|---|---|---|
| [credentials.md](./credentials.md) | — | クレデンシャル台帳（キー名と用途のみ・値は書かない） |
| [動作検証-vertex.md](./動作検証-vertex.md) | 1 / Vertex AI | ✅ 完走 |
| [動作検証-sagemaker.md](./動作検証-sagemaker.md) | 2 / SageMaker AI | ✅ 完走 |
| [動作検証-databricks.md](./動作検証-databricks.md) | 3 / Databricks | ✅ 完走 |
| [動作検証-azureml.md](./動作検証-azureml.md) | 4 / Azure ML | ✅ 完走（契約ゲート2枚を越えた） |
| [動作検証-snowflake.md](./動作検証-snowflake.md) | 5 / Snowflake | ✅ 完走 |

## 動作検証 runbook を基盤ごとに分けている理由

完了条件の8項目は5基盤で共通だが、**それを満たす手順は共通化できない**。
配布物（イメージ / wheel / zip）、入出力の渡し方、deploy というリソースの有無、
1件推論の payload 型、teardown 後に残るものが全部違う。
1本の汎用手順に畳むと、その差（＝このラボが測ろうとしているもの）が手順書から消える。

`docs/04_workflows.md` は**共通の骨格**（make ターゲットの並び）、
ここの各 runbook は**基盤ごとの実手順と合否判定**を持つ。役割を混ぜない。

## 共通の完了条件8項目

各 runbook の手順表はこの8項目に対応する。判定結果は
`docs/comparison/<NN>_<platform>.md` の同じ表へ転記する（⑧はそのページを書くこと自体）。

| # | 条件 | 記録先 |
|---|---|---|
| ① | terraform apply | `infra_events`（所要・リソース数） |
| ② | 学習ジョブ成功（**失敗試行も記録済み**） | `ml_runs`（stage=train、失敗も1行） |
| ③ | Neon へメトリクス到達（direct / collected） | `ml_runs.write_path` |
| ④ | モデル登録 | `ml_runs`（stage=register） |
| ⑤ | 1件オンライン推論 | `ml_runs`（stage=predict） |
| ⑥ | terraform destroy | `infra_events` |
| ⑦ | 残留リソース記録 | `infra_events.residual_resources` |
| ⑧ | 比較レポート1ページ記述 | `docs/comparison/` |

**失敗を消さない。** 最小権限で通るまでの試行回数がこのラボの本命の計測値なので、
権限エラーは広い権限を貼って回避せず、失敗 run を残したまま最小権限を1段ずつ足す。

## 操作面の差分（詳細は各 runbook）

| | Vertex | SageMaker | Azure ML | Databricks | Snowflake |
|---|---|---|---|---|---|
| ENV（terraform） | `gcp-dev` | `aws-dev` | `azure-dev` | `dbx-dev` | `sf-dev` |
| PLATFORM（実行） | `vertex` | `sagemaker` | `azureml` | `databricks` | `snowflake` |
| リージョン | us-central1 | ap-northeast-1 | japaneast | workspace 依存 | account 依存 |
| 配布物 | 学習/推論イメージ | 学習/推論イメージ | 学習/推論イメージ | wheel | zip |
| 配布先 | Artifact Registry | ECR | ACR | UC Volume | stage |
| deploy | Endpoint | Model→Config→Endpoint | Endpoint+Deployment+traffic | Serving（scale-to-zero） | **無し**（既定版の切替のみ） |
| 推論 payload | 辞書 | 文字列 | ファイル | dataframe_records | SQL |
| Neon 到達（**実測**） | direct ✅ | direct ✅ | direct ✅ | collected ✅ | collected ✅ |
| apply 試行回数（実測） | **1** | **1** | 3 | 2 | 4 |
| destroy 試行回数（実測） | **1** | 2 | 2 | 3 + 手動 | **1** |
| 残留（実測） | WARN 1 | WARN 3 | IaC 管理外 1 | **0** | WARN 2 |

**Neon 到達は5基盤とも仮説どおりだった**（Tier A = direct / Tier B = collected）。
ただし collected に落ちた理由は基盤ごとに違う（Databricks は psycopg 不在、
Snowflake は External Access Integration がトライアルで作れない）。

構造差の一次情報は
reuse-asset-import-map.md「実装で確定した比較材料」。
結論は [comparison/selection-checklist.md](../comparison/selection-checklist.md)、
残留は [comparison/residual-resources.md](../comparison/residual-resources.md)。

## 全 Phase 共通の前提（1回だけ）

```bash
make setup                      # venv + 依存
make db-migrate                 # sql/schema.sql を Neon direct endpoint へ
make neon-create && make neon-load
make dataset-export             # data/california_housing.parquet + checksum
make train                      # Phase 0 のローカル基準値を再現（RMSE 0.4368055090296257）
```

基準値が一致しない状態で Phase へ進まない。5基盤の metric parity はこの値との比較で判定する
（[docs/comparison/00_method.md](../comparison/00_method.md)）。

秘密は Doppler 経由でのみ渡る（`doppler run --`）。make の各ターゲットに埋め込み済み。

## 全 Phase 共通の停止条件

- deploy 後に teardown 前提を崩さない。**Tier A のエンドポイントは常時課金**。
- quota / trial 期限で手順どおりに進めない → 回避策を実装せず、
  **その事実を `ml_runs` の失敗 run と comparison ページに残す**（それが比較材料）。
- 同じ検証が2回、違う理由で失敗したら止めて owner に確認する
  （`docs/specs/runtime-protocol.md`）。

## 検証に使う SELECT

数値は手で数えない。`sql/comparison_queries.sql` の5本（metric parity / permission friction /
failure_class 内訳 / stage 別所要 / teardown 品質 / 到達経路内訳）から起こす。
