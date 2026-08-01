# 流用資産マップ（移植元の記録）

> ✅ **消化済み（2026-08-01）**。5基盤すべて完走し、一次データは `docs/comparison/` に載った。
> 移植の記録としてのみ残す。

更新: 2026-08-01。**移植すべき残作業はゼロ**（A / B / C / D 実装済み）。
このファイルは「これから移植する在庫リスト」ではなく、**何をどこから借りたかの記録**。
レポートの再現性の一部なので、`docs/comparison/` に一次データが載り、
そこへ出典として取り込めた時点で削除する。

実装の残ギャップは [仕様準拠監査-2026-08-01.md](./仕様準拠監査-2026-08-01.md) が正本（このファイルではない）。

## 移植の記録（何をどこから借りたか）

| 実装 | 主な流用元 | 移植時に変えた点 |
|---|---|---|
| A-1 Vertex | `kaggle-bronze-gcp/src/runner/{experiment/vertex_run,model/register,model/deploy}.py` | YAML 設定 → `VertexConfig` / 出力先を `--output-uri` → `AIP_MODEL_DIR` / guarded sync（gcloud subprocess）は不採用 |
| A-2 SageMaker | `eks-app-mlops-platform-v2/mlops/src/train.py`（boto3 の型）+ `TMP/amazon-sagemaker-ml-pipeline-deploy-with-terraform`（依存順序） | sagemaker SDK ではなく boto3 低レベル API / hyperparameters は JSON を1キーに畳む / `AmazonSageMakerFullAccess` は貼らない |
| A-3 Azure ML | `TMP/azureml-examples`（CLI v2 YAML）→ SDK v2 へ読み替え | ローカル資産ゼロのため新規。トラフィック配分を明示的な3手目に |
| A-4 Databricks | `study-snowflake-databricks/src/databricks_job_trigger.py` + `TMP/terraform-provider-databricks` docs | ジョブ定義は Terraform 側、adapter は起動のみ / job ID は名前から引く / MLflow 非依存 |
| A-5 Snowflake | `eks-app-mlops-platform-v2/.../snowflake_adapter.py` + `TMP/snowflake-ml-python`（API を実物確認） | 登録は「復元したモデル」を log_model / deploy はインフラを作らない |
| A-6 共通の型 | 自前（5 adapter の重複を集約） | `platforms/contracts/tracking.py`。**記録の形だけ共通化し、SDK 呼び出しの形は共通化しない**（差が比較材料なので隠さない） |
| B 残留検査 | `gcp-search-mlops-gke/scripts/ops/destroy_check.py`（402行） | FAIL/WARN/ERROR と exit code 規約を踏襲。5基盤横断のためクライアント注入式に |
| C コスト | `kaggle-bronze-gcp/src/runner/ops/costs.py` + `eks-app-mlops-platform/apps/aws-resource-monitor` | **取得不能を 0 円にしない**（0 と混ぜると比較表が嘘になる） |
| D 学習イメージ | `kaggle-bronze-gcp/infra/Dockerfile` | libgomp1 / uv / 非 root / `CODE_REVISION` をビルド引数で焼き込む（コンテナ内に .git は無い） |

`src/core/ml` の母体は starter-kit `python/ml` の California Housing + LightGBM 実装。

## 実装で確定した比較材料（`docs/comparison/` の骨格）

コード実測から起こした構造差。クラウド実測前に確定している唯一の一次情報なので、
各基盤ページを書くときはこの表を出発点にする。

| | Vertex | SageMaker | Azure ML | Databricks | Snowflake |
|---|---|---|---|---|---|
| 実行資源 | CustomJob | TrainingJob | CommandJob | Job 起動のみ | DDL + CALL |
| 入出力の渡し方 | 環境変数 | 固定パス `/opt/ml` | マウント宣言 | wheel + params | sproc 引数 |
| 登録 | URI + alias | URI + **承認** | 名前+自動採番 | UC 3階層 | **モデル実体** |
| デプロイ | Endpoint（器を先に作れる） | Model→Config→Endpoint | Endpoint+Deployment+traffic | serving（scale-to-zero） | **リソース無し** |
| 1件推論 | 辞書 | 文字列 | **ファイル** | dataframe_records | warehouse 推論 |
| teardown 後の残留 | なし | Model Package | Model | UC 版 + wheel | 版 + stage + **Fail-safe（消せない）** |

## 流用時の注意（実クラウド接続時に有効）

1. **eks 3リポジトリのローカル clone が壊れている**（git object store 破損）。B / C の AWS 面を
   さらに深掘りするなら再 clone が要る（今回の実装に必要な `train.py` は読めた）。
2. **starter-kit 配下は書き換え禁止**（check-drift の byte 一致核）。
3. Snowflake のサンプルはデータ層が別物 → [snowflake-phase-precheck.md](./snowflake-phase-precheck.md) の「実装時の既知の罠」。

## 出典

- 設計ブレスト: [../../archive/managed-ml-platform-comparison-brainstorm-v2.md](../../archive/managed-ml-platform-comparison-brainstorm-v2.md)
- 外部 OSS 調査の生ログ: [../../archive/github-oss-reuse-survey.md](../../archive/github-oss-reuse-survey.md)
- 関連 precheck: [databricks-phase-precheck.md](./databricks-phase-precheck.md) / [snowflake-phase-precheck.md](./snowflake-phase-precheck.md)
