# 02 アーキテクチャ

> 出典: 設計ブレスト v2（[archive/managed-ml-platform-comparison-brainstorm-v2.md](./archive/managed-ml-platform-comparison-brainstorm-v2.md)）から蒸留。何を満たすか（要件・制約）は [01_requirements.md](./01_requirements.md) が正本。

## 概要

```text
src/core/ml (単一SHA・依存最小)
  ├─ Tier A: 単一コンテナイメージ + entrypoint シム
  │    -> Vertex AI / SageMaker AI / Azure ML
  └─ Tier B: 同一Pythonパッケージ
       -> Databricks (wheel) / Snowflake (stage upload)

全基盤 ──→ Neon PostgreSQL (pooled endpoint) ──→ SELECT で比較
              ↑ 到達不能時は JSONL fallback + make collect

  -> docs/comparison/ に実測レポート（主成果物）
```

コードは計測装置。主役は `docs/comparison/` の実測レポートと選定チェックリスト。Neon は単なる保存先ではなく**全基盤の計測到達点**であり、到達経路の重さ自体を計測する。

## 2階層構造

| | Tier A: コンテナ実行型 | Tier B: データ基盤内蔵型 |
|---|---|---|
| 基盤 | Vertex AI / SageMaker AI / Azure ML | Databricks / Snowflake |
| 統一単位 | 同一コンテナイメージ（BYOC） | 同一Pythonパッケージ（wheel / stage upload） |
| データ | オブジェクトストレージへ出してから計算 | データのある場所で計算 |
| 権限 | IAM ロール / サービスアカウント / マネージドID | Unity Catalog / Snowflake RBAC（データガバナンスと一体） |

両 Tier を貫く統一単位は **`src/core/ml` の同一 git SHA**（v1 の「同一コンテナ」は Tier B で成立しないため引き上げ）。contract test で `code_revision` 一致を検証する。

## 成長フェーズ

| フェーズ | 内容 | 位置付け |
|---|---|---|
| Phase 0 | ローカル基準実装 | 基準値の確立（PMLE前） |
| Phase 1 | Vertex AI | アンカー。2セッション。既存の make ターゲット / destroy-all / contract test を流用（PMLE前） |
| Phase 2 | SageMaker AI | 差分測定（PMLE後） |
| Phase 3 | Databricks | ML Associate 学習と併走。Terraform 網羅度の上限を先に確認。scale-to-zero でアイドル課金リスク低 |
| Phase 4 | Azure ML | 条件付き（Phase 3 完了時に go/no-go）。Tier A 3つ目で限界効用が最も低い |
| Phase 5 | Snowflake | SnowPro Core 着手がトリガ。トライアル期限が実質のタイムボックスのため分散実行せず一気に完走 |

既存リポジトリからの流用元の実体（`src/core/ml` の母体 = starter-kit `python/ml` の California Housing + LightGBM 実装、Phase 1 の母体 = `kaggle-bronze-gcp`、destroy-all / contract test = `gcp-search-mlops-gke` 等）と移植時に変えた点は reuse-asset-import-map.md に記録している。

## 構成要素

| 構成要素 | 役割 | 担当パス |
|---|---|---|
| 共通MLコード | 5基盤共通。ここの git SHA が比較成立の担保。依存は lightgbm / scikit-learn / pandas / pyarrow のみ | `src/core/ml/`（dataset / preprocess / train / evaluate / schemas） |
| 推論アプリ (Tier A) | 1つの FastAPI アプリで3基盤の推論契約を同時に満たす | `src/core/app/api/main.py` |
| Neon アクセス層 | Neon 接続の正本（pooled / direct 使い分け・PgBouncer 制約・cold start retry）と学習入力データ（`california_housing`）のロード・読み出し。テレメトリもこの接続層を使う | `src/platforms/neon/`（connection / schema / records / repository / load） |
| テレメトリ | 1試行 = ml_runs 1行を**失敗時も必ず**残す `track()`（attempt / failure_class が permission friction の一次データ）。core 側は stdlib のみで、Neon 書き込み（psycopg）は `platforms/neon/run_sink.py`、到達不能環境は JSONL fallback | `src/core/telemetry/`（tracking / sinks / schemas）+ `src/platforms/neon/run_sink.py` |
| **ジョブ内テレメトリ** | **ジョブの中から** ml_runs を書く層。Neon 直 INSERT → 不能なら成果物と同じ場所へ JSONL。`write_path` の一次データはここでしか取れない（adapter からの INSERT は「オーケストレータの egress」であってジョブの egress ではない）。所有者規約は同モジュールの docstring | `src/platforms/neon/job_record.py` |
| Tier A adapter | 学習ジョブ投入・モデル登録・デプロイの SDK 呼び出し | `src/platforms/{vertex,sagemaker,azureml}/` |
| Tier B adapter | Databricks = wheel build + Jobs API / Snowflake = stage upload + stored procedure。配布物は `contracts/packaging.py` が stamp 検証してからビルド | `src/platforms/{databricks,snowflake}/` |
| **横断（shared）** | `platforms/` 直下の判別基準は「**これは基盤か？**」の一問。5基盤 + `neon/`（計測到達点・比較対象ではない）以外は全部ここ。契約・設定解決・組み立て・ArtifactStore・名前導出 | `src/platforms/shared/`（`__init__.py` に置く基準を明記） |
| Terraform 入力解決 | 人が決める値（`env/config.yaml` の `terraform:` 節）と秘密・個人識別子（Doppler）から `-var` を組み立てる。**シェルの `export TF_VAR_*` という第3の出所を持たない**。未解決なら terraform 起動前に落ちる | `src/platforms/shared/terraform_vars.py` |
| 配布物の名前 | wheel 名を pyproject の name/version から導出（config / adapter / Terraform / テストの4箇所に版を写さない） | `src/platforms/shared/packaging_names.py` |
| adapter 組み立て | 解決済み設定から adapter を作る。**deploy に渡す値の基盤差**もここに集約（差は隠さず1箇所に集める） | `src/platforms/shared/factory.py` |
| ArtifactStore | 入力 Parquet の配布と成果物の回収。GCS / S3 実装済み、Blob は Phase 4。Tier B は adapter 側の既存経路（Volume / stage） | `src/platforms/shared/artifacts.py` |
| フェーズ実行 | Golden Path ②〜⑤を回す runner（前段が失敗したら止める） | `scripts/run_phase.py` |
| インフラ操作の計測 | `terraform apply` / `destroy` の所要とリソース数を infra_events へ（Golden Path ①） | `scripts/run_terraform.py` |
| 学習コンテナ (Tier A) | 単一イメージ + プラットフォーム別 entrypoint シム。シムは学習の後に `job_record` を呼ぶ | `docker/training/` |
| 推論コンテナ (Tier A) | 1イメージで3契約。Vertex の `AIP_STORAGE_URI`（gs://）取得は stdlib のみの起動シム | `docker/serving/` |
| インフラ定義 | 静的基盤の Terraform モジュール / 環境 | `infra/modules/{gcp,aws,azure,databricks,snowflake,neon}/` `infra/environments/` |
| 設定解決 | **人が決める値（`env/config.yaml`）と apply が決める値（`terraform output -json`）を分ける**。解決順は 既定 < config.yaml < outputs < 環境変数（`MCML_<PLATFORM>_<FIELD>`）。秘密は扱わず各 SDK の資格情報チェーンに任せる | `src/platforms/shared/config.py` + `env/config.yaml` |
| 残留リソース検査 | destroy 後に残るものを5基盤横断で列挙し infra_events へ記録。それ自体が転用可能な成果物 | `scripts/check_residual.py` |
| fallback 収集 | 各基盤ストレージに出力された JSONL をローカルから Neon へ流し込む（`make collect`） | `scripts/collect_jsonl.py` |
| 計測スキーマ | run / infra_event / cost の3テーブル DDL | `sql/schema.sql` |
| 比較クエリ | metric parity / permission friction / lifecycle / teardown の定番 SELECT | `sql/comparison_queries.sql` |
| contract test | 全基盤の run が同一SHAであることの検証 | `tests/test_code_revision_parity.py` |
| metric parity test | 全基盤で RMSE が一致することの検証 | `tests/test_metric_parity.py` |
| 比較レポート | 前提宣言（method）+ 基盤別1ページ + 選定チェックリスト + 残留比較（主成果物） | `docs/comparison/` |
| エージェントガイド | Codex / 他エージェント向けの repo ガイド | `AGENTS.md` |
| Claude ガイド | Claude Code の司令ルール | `CLAUDE.md` |
| タスク文書 | 一回性の作業計画・実装タスク | `docs/tasks/` |
| Claude skills | Claude Code で繰り返し使う作業手順 | `.claude/skills/` |

## 実行契約の差（設計の中心）

### Tier A: 学習側の契約差とシム

```text
Vertex    : AIP_MODEL_DIR に成果物を書く / 引数は自由
SageMaker : /opt/ml/input/data/<channel>, /opt/ml/model, /opt/ml/output
            hyperparameters は /opt/ml/input/config/hyperparameters.json
Azure ML  : command job の outputs マウントパス / 引数は自由
```

解法: 共通CLI + プラットフォーム別シム。各シム（`docker/training/entrypoint_*.sh`）は最終的に同じコマンドへ落とす。

```bash
python -m core.ml.cli --input "$INPUT_DIR" --output "$MODEL_DIR" --params "$PARAMS_JSON"
```

### Tier A: 推論側は1イメージで3契約

境界でリクエスト形式だけ変換し、中心の `predict()` は1つ:

```text
/health + /predict      # Vertex ({"instances": [...]} -> {"predictions": [...]})
/ping + /invocations    # SageMaker (port 8080)
/score                  # Azure ML managed online endpoint
```

### Tier B: 契約はコンテナではなくパッケージ

```text
Databricks
  投入   : Jobs API / Python wheel task（job cluster または serverless）
  追跡   : MLflow（基盤内蔵）
  登録   : Unity Catalog Models（catalog.schema.model の3階層名前空間）
  推論   : Mosaic AI Model Serving（scale-to-zero 可）
  同一性 : src/core/ml を wheel 化して配布

Snowflake
  投入   : Snowpark Python stored procedure（warehouse実行）
  追跡   : 内蔵の実験追跡は限定的 → Neon 側の ml_runs を一次記録とする
  登録   : Snowflake Model Registry（スキーマ内オブジェクト）
  推論   : Model Registry 経由の SQL 関数（SPCS は差分メモ止まり・主経路にしない）
  同一性 : src/core/ml を stage へアップロードして import
```

## Neon 集約（計測到達点）

- 書き込みは **pooled endpoint**（PgBouncer / transaction mode）、DDL・migration は direct endpoint。
- transaction pooling の実装注意（`src/platforms/neon/connection.py` に集約済み）: クライアント側プールを重ねない / prepared statements を無効化（psycopg は `prepare_threshold=None`。**PgBouncer 1.22 以降はプロトコルレベルの prepare が動くため必須ではないが、短命ジョブでは利得が無いので保守的に無効のままにする**）/ Neon compute の suspend 明けは初回接続が遅い → 3回リトライ。
- transaction mode で使えないもの（pooled 経由で書くコードの制約）: `SET` / `RESET`、`LISTEN` / `NOTIFY`、SQL レベルの `PREPARE` / `DEALLOCATE`、一時テーブル、セッションレベル advisory lock。DDL・migration が direct なのはこの制約のため（Neon 公式ドキュメント "Connection pooling" 2026-07-31 参照）。
- 短命ジョブからの書き込みなので接続は開いて即閉じる。**学習ジョブ本体とトランザクションを共有しない**（学習失敗時にも記録が残る必要がある）。
- 到達経路: primary = ジョブ内から直接 INSERT / fallback = JSONL を各基盤ストレージへ出力し `make collect` でローカルから流し込み。どちらを使ったかを `ml_runs.write_path`（direct / collected）に記録し、そのままレポートの一行にする。
- **run 行の所有者**（5基盤共通・実装は `src/platforms/neon/job_record.py`）: 学習の**成功行はジョブ側**が書き、**投入失敗行は adapter 側**が書く（投入前に落ちる iam / quota はジョブが起動しないので adapter でしか観測できない）。adapter が成功行も書くと `write_path` が「オーケストレータから届いた」を意味してしまい、この比較軸が測れなくなる。`run_id` は adapter が発番してジョブへ渡し、`attempt` も adapter が Neon で数えて渡す（ジョブ側は Neon へ届かない可能性があり、そこで数え直せない）。
- Neon 接続情報は Tier A ではジョブ定義の env で渡す（`platforms/shared/contracts/tracking.py` の `telemetry_env()`）。**`CODE_REVISION` は渡さない** —— コンテナにビルド時に焼き込んだ値を上書きすると「実際に動いたコード」と記録がずれ、同一SHA担保が壊れる。Tier B は Snowflake が JSONL 一択（warehouse に psycopg が無い）、Databricks は serverless への secret 受け渡しが Phase 3 の precheck 項目。
- Tier B から外部 PostgreSQL への到達は宣言的オブジェクト（Snowflake: network rule / secret / external access integration、Databricks: serverless egress 設定）を経由する。この差を `failure_class = 'network'` で記録する。

## 計測データ層

- run単位（`ml_runs`）/ インフラ操作単位（`infra_events`）/ コスト（`cost_snapshots`）の3テーブルに分離。混在させると NULL が増え、コストは請求反映に1〜2日遅れるため実行時に確定しない。
- v2 での拡張: `platform`（5値）/ `tier`（A|B）/ `unification_unit`（container|package）/ `write_path`（direct|collected）、`failure_class` に package / network を追加。
- 核心フィールドは `failure_class` と `attempt`。「最小権限で学習ジョブが通るまでに何回直したか」を出す permission friction クエリが本命。
- `code_revision`（`src/core/ml` の git SHA）は必須。contract test + metric parity test で担保。
- 保存先は Neon（数千行なので通常の PostgreSQL で十分）。DDL 正本は `sql/schema.sql`、比較 SELECT の正本は `sql/comparison_queries.sql`、詳細は [05_data_model.md](./05_data_model.md) に定義する。

## 共通ML仕様（全5基盤で同一）

```text
Data     : sklearn.datasets.fetch_california_housing
           取得後 Parquet 化して各基盤のストレージへ配置
Target   : MedHouseVal
Baseline : RandomForestRegressor  <- **既定では走らせない**（下記）
Main     : LightGBM Regressor   <- 実務(LambdaRank)との連続性を優先
Metrics  : RMSE, MAE, R2        <- 同一SHAで同一メトリクスが再現されることの確認用
Seed     : 固定
```

baseline は精度の優劣比較（非対象）のためではなく、**配管の健全性チェック**
（列の取り違え・目的変数の混入・分割のずれの検出）。5基盤のジョブで毎回2モデル学習すると
比較したい実行時間に無関係な負荷が乗るため既定は off で、Phase 0 のローカル基準確立
（`make BASELINE=1 train`）でだけ有効にする。メトリクスは `baseline_` 接頭辞で分ける。

パッケージ可用性の制約（依存最小の理由）: Snowflake warehouse Python は Anaconda channel 限定でバージョン固定が効きにくく、Databricks は ML Runtime プリインストール版と wheel 依存が衝突しうる。

## Golden Path とアーキテクチャ

要件で定義した Critical User Journey / Golden Path（[01_requirements.md](./01_requirements.md)）が、どの構成要素・境界を通るかを写像する。設計判断（責務配置・境界・依存）は、まずこの一本道を壊さない・遅くしないことを優先条件にする。

| Golden Path ステップ | 通る構成要素 | 通る境界 / データ | 単一障害点（切れると journey が止まる） |
|---|---|---|---|
| 1. terraform apply | `infra/` | Terraform state / infra_events | クラウド側 IAM・quota、トライアル期限（Snowflake） |
| 2. 学習ジョブ投入・成功 | Tier A: `docker/training` + adapter / Tier B: wheel・stage + adapter | 各基盤の実行契約 / ml_runs | 契約シム・パッケージ依存の崩れ、`code_revision` 不一致 |
| 3. Neon 到達・登録・1件推論 | `src/core/telemetry/tracking.py` + `src/platforms/neon/run_sink.py`（+ fallback `sinks.JsonlRunSink` → `collect_jsonl`）+ `src/core/app` / Tier B レジストリ | pooled endpoint / 外部接続オブジェクト / write_path | Tier B の外部ネットワーク到達性（fallback で journey は継続） |
| 4. destroy・残留記録・レポート記述 | `infra/` + `scripts/check_residual.py` + `docs/comparison/` | infra_events.residual_resources | レポート未記述（次フェーズをブロック） |

- 新しい構成要素・adapter・依存を足すときは、この表の Golden Path 上に不要な単一障害点を増やしていないかを確認する。
- Golden Path 上の構成要素の変更は、リリース Runbook の smoke 対象（[08_release_runbook.md](./08_release_runbook.md)）と整合させる。

## 境界

- **Terraform / SDK の境界**: 静的基盤 = Terraform、ジョブ実行・登録・デプロイ = SDK/CLI/SQL。`terraform apply` に学習実行を含めると、インフラ状態とML実行履歴が state 上で混ざり両方の再現性が落ちる。

| 基盤 | Terraform でカバー | SDK/CLI/SQL に残る |
|---|---|---|
| Vertex AI | IAM, GCS, Artifact Registry, Endpoint（器） | Custom Job, Experiment, Model Upload, Deploy |
| SageMaker AI | S3, ECR, IAM Role, Model Package Group, Model, Endpoint Config, Endpoint | Training Job, Model Registry 登録・承認 |
| Azure ML | Resource Group, Storage, ACR, Key Vault, App Insights, Workspace, Compute Cluster | Command Job, Model登録, Managed Online Endpoint 更新 |
| Databricks | Catalog, Schema, Grants, Cluster/Policy, Job, Registered Model, Serving Endpoint | ジョブ実行トリガ, MLflow run, モデルバージョン昇格 |
| Snowflake | Database, Schema, Warehouse, Role, Grants, Stage, Network Rule, Secret, External Access Integration | Stored Procedure 実行, Model Registry 登録, サービス関数作成 |

- 上表から導く構造仮説（レポートで実測検証する）: Databricks が Terraform 網羅度最大（ガバナンスオブジェクトまで）、Snowflake は器は完全コード化できるがレジストリ側が SQL/SDK に残る、Azure ML は周辺依存で初期構築量最大、Vertex は ML 固有リソースの対応が限定的。
- **アイドル課金と撤退の設計**: Tier A のマネージドエンドポイントは常時課金のため各フェーズ末に必ず destroy。Databricks は job cluster 自動終了 + serving scale-to-zero を必須設定にする。Snowflake は warehouse auto-suspend。残留検査は `check_residual.py` に集約（Tier B は Time Travel / Fail-safe / カタログ内オブジェクト / stage 成果物が残留候補）。
- ソースコードは、別の境界を定義しない限り `src/` 配下に置く。
- 非機密の設定は `env/config.yaml` に置く。ただし **apply が決める値（バケット名・ARN・イメージ URI）は置かない**（`terraform output -json` の生成物を正とする。手書きは必ず腐る）。
- ローカル秘密情報は ignore したまま。共有・本番の秘密情報は Doppler などの secret manager に置く。
- Codex が `.claude/rules/` や `.claude/skills/` を読む前提にしない。Codex / 他エージェント向けに永続させたい指針は `AGENTS.md` に置く。

## 関連タスク

- 構造変更、責務移動、adapter 追加、共通化は、実装前に `docs/tasks/03_active/` へ task を作る。
- 中規模以上の変更では、task に Skeleton / Plan / Acceptance Criteria を書いてから実装する。
- 確定した設計判断は task から `docs/decisions/` またはこの文書へ昇格する。
