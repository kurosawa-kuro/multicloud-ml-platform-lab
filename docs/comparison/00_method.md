# 00 比較の前提と、測れないことの宣言

> **前提は確定済み**（基盤別ページより先に書き終えてある）。
> 前提を後から書くと、出た結果に都合よく前提を合わせてしまうため、以降は
> **実測が予想と食い違ってもこのページを書き換えない**。
> 食い違いは各基盤ページに「予想 → 実測 → 差分」で残す。
>
> **実測完了: 5基盤すべて 8/8（2026-08-01）。**
> Phase 1 Vertex AI ✅ / Phase 2 SageMaker ✅ / Phase 3 Databricks ✅ /
> Phase 4 Azure ML ✅ / Phase 5 Snowflake ✅。
> 結論は [selection-checklist.md](./selection-checklist.md)、残留は
> [residual-resources.md](./residual-resources.md)。
> **RMSE `0.4368055090296257` と 1件推論の予測値 `4.183217948107466` は5基盤とも一致した**
> （＝差が出たのはモデルの出力ではなく、そこへ到達するまでの経路）。

## 何を測るか

精度比較ではない。5基盤で同一データ・同一SHAのコードを回したときの、
**選定時に効く差分**を測る。

- 権限設計に要した試行回数（最小権限で学習ジョブが通るまで何回直したか）
- IaC（Terraform）で管理できる境界 / SDK・CLI・SQL に残る境界
- 外部DB（Neon PostgreSQL）への到達経路と設定の重さ、到達可否そのもの
- 撤退後の残留リソース
- アイドル時課金の構造差

## 測れないこと（先に宣言する）

宣言せずに数字だけ出すと、読み手が過剰に一般化する。

- **性能・スケーラビリティ**: 20,640行では測れない。データ近接の優位も出ない
- **精度**: 同一SHAなら一致するのが前提。差が出たら基盤の優劣ではなく前提の破綻
- **課金の絶対額**: 無料枠・トライアル・リージョン差が乗るため他者に移植できない
- **エンタープライズ機能**: 個人アカウントの範囲で触れるものに限る
- **機能比較マトリクス**: [thoughtworks/mlops-platforms](https://github.com/thoughtworks/mlops-platforms) が公開済み。
  複製せず参照先として使う
- **failure_class の自動分類精度**: 例外メッセージの文字列マッチによる推定
  （`core/telemetry/tracking.py`）。Phase 1 の実失敗で辞書を育てる前提であり、
  初期の誤分類は `error_excerpt` を根拠に手で再分類する。分類の信頼度より
  「失敗も全件記録されていること」を優先している

## 比較が成立する条件

以下が崩れた状態で出した数値は無効。

| 条件 | 担保 |
|---|---|
| 同一データ | sklearn 版 California Housing のみ（Kaggle 版混入禁止） |
| 同一コード | `ml_runs.code_revision` が全基盤一致（tests/test_code_revision_parity.py） |
| 同一メトリクス | 全基盤で RMSE 一致（tests/test_metric_parity.py） |
| 同一 seed | src/core/ml で固定 |

## Phase 0 ローカル基準値（2026-07-31 実測）

5基盤の metric parity は、まずこのローカル実測値との一致で判定する。

| 項目 | 値 |
|---|---|
| RMSE | **0.4368055090296257** |
| MAE | 0.2840129606543999 |
| R2 | 0.8543973248910732 |
| best_iteration | 735 |
| seed / 分割 | 42 / train 64% : valid 16% : test 20%（`row_id` 順ソート後に分割） |
| 実行環境 | lightgbm 4.6.0 / scikit-learn 1.9.0 / pandas 2.3.3（`pyproject.toml` で minor 固定） |
| データ | Neon `california_housing` 20,640行 → `data/california_housing.parquet`（sha256 記録済み） |
| 再現手順 | `make dataset-export` → `make train`（同一入力・同一 seed で完全一致を `tests/test_metric_parity.py` が固定） |

参考: kaggle-bronze-gcp の独立実装は RMSE 0.44498。分割条件が異なるため一致はせず、
桁の整合確認（sanity check）にのみ使う。

## 条件が揃わない箇所（既知の非対称）

揃えられなかったものは隠さず書く。

- **リージョン**: **5基盤で揃っていない**。Vertex = us-central1（既存 GCP 資産と同一）、
  SageMaker = ap-northeast-1、Azure ML = japaneast、Databricks / Snowflake は
  アカウント作成時のリージョンに従う。Neon は Singapore（`aws-ap-southeast-1`。
  東京リージョンが無いため）。したがって **Neon 到達のクロスリージョン遅延は基盤ごとに違う**。
  所要時間の絶対値を基盤間で直接比べず、各ページにリージョンを明記した上で
  「同一リージョン内の操作」（apply / 学習ジョブ本体）を主に比較する。
  正本は `infra/environments/*/variables.tf` の既定値と `env/config.yaml`
- **課金プラン**: Azure は FreeTrial 枠。vCPU quota が他基盤と揃わない可能性
- **フェーズ間の時期差**: Phase 1 と Phase 5 の間に各サービスの仕様変更が入りうる。
  各ページに実施日を必ず書く

## 数値の出どころ

レポートの表は `sql/comparison_queries.sql` の結果から起こす。
手で数えた値・記憶で書いた値を混ぜない。出典クエリを各表に併記する。

### friction の集計境界（5基盤で固定・2026-08-01 確定）

摩擦は **2層に分けて別掲する**。合算した数字は出さない。

| 層 | 対象 | 記録先 | 何を表すか |
|---|---|---|---|
| **run friction** | 学習・登録・デプロイ・推論の試行 | `ml_runs.attempt` / `failure_class` | **実行時**に何回直したか |
| **infra friction** | `terraform apply` / `destroy` の試行 | `infra_events` | **構築時**に何回直したか |

分けるのは、**同じ「権限の失敗」でも層が違うと意味が違う**ため。
Snowflake の `Cannot grant or revoke USAGE on an internal staging location` は
権限モデルの実測だが `infra_events` 側にあり、`ml_runs` の内訳には現れない。
合算すると「実行時に何回権限を直したか」が読めなくなり、分離しないと
「構築時の摩擦がゼロの基盤」と混同される。

各基盤ページの「failure_class の内訳」は **run friction のみ**。
infra friction は「apply 試行回数」として別行に書く。
**両方を足した数を指標として使わない。**

## 失敗の扱い

**失敗した試行こそが一次データ。** 成功した手順だけ書くと
「最小権限で通るまで何回直したか」が消え、このプロジェクトの主目的が失われる。
`ml_runs` には失敗 run も `attempt` を増やして必ず記録する。
