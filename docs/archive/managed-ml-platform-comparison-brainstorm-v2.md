# マネージドML基盤 比較プロジェクト 設計方針 v2（生ブレスト退避）

> **退避元**: チャット貼り付け（2026-07-31 に distill-spec で退避）
> **蒸留先**: 仕様 → `docs/01_requirements.md` / 基礎設計 → `docs/02_architecture.md`
> **先行版**: v1 は [managed-ml-platform-comparison-brainstorm.md](./managed-ml-platform-comparison-brainstorm.md)
> **権威**: この文書は archive であり権威順位は最下位（`docs/00_index.md` 参照）。確定定義は 01/02 が正本。

---

# マネージドML基盤 比較プロジェクト 設計方針 (v2)

> リポジトリ名案: `managed-ml-platform-comparison`
> 対象: Vertex AI / SageMaker AI / Azure ML / Databricks / Snowflake
> 題材: California Housing（**全基盤で同一**。データセットは題材ではなく固定具 / fixture）
> 実験メタデータ: Neon PostgreSQL（プール接続経由で全基盤から書き込み、SELECTで比較）

---

## 0. v1からの変更点

| # | 変更 | 理由 |
|---|------|------|
| 1 | Databricks / Snowflake を追加し、**2階層構造**に再編 | 5基盤は同一カテゴリではない |
| 2 | 統一単位を「同一コンテナイメージ」→「`src/common` の同一SHA」へ引き上げ | Tier B は BYOC が主経路ではない |
| 3 | Neon を「保存先」から「**全基盤の計測到達点**」へ格上げ | 到達可否そのものが比較軸になる |
| 4 | Feature Store を **全5基盤で明示的に不使用** | Databricks / Snowflake にもFS機能があるため明文化が必要 |
| 5 | 実施順序を Databricks 先行へ変更（Azureが条件付きに降格） | 資格計画・Terraform網羅度・アイドル課金の3点 |

v1からの継続方針（変更なし）:

- 成果物はコードではなく **比較レポート＋選定チェックリスト**
- 失敗を一級データとして記録する計測スキーマ
- `pg_mooncake` は backlog（合成ログでは結論が使えない）
- Terraform は静的基盤まで。ジョブ実行はSDK側

---

## 1. 2階層構造

### Tier A: コンテナ実行型（hyperscaler managed ML）

```text
Vertex AI / SageMaker AI / Azure ML

統一単位 : 同一コンテナイメージ
データ   : オブジェクトストレージへ出してから計算
権限     : IAM ロール / サービスアカウント / マネージドID
```

### Tier B: データ基盤内蔵型

```text
Databricks / Snowflake

統一単位 : 同一Pythonパッケージ（wheel / stage upload）
データ   : データのある場所で計算
権限     : Unity Catalog / Snowflake RBAC（データガバナンスと一体）
```

### この分割から出る新しい比較軸

California Housing は約2万行なので、**「データの近くで計算する」性能優位はこの規模では測れません**。
測れるのは次です。

```text
権限モデルがデータガバナンスと一体か、分離しているか
外部ネットワークへの出方（egress か、宣言的な統合オブジェクトか）
モデルの所在（レジストリ独立か、カタログ内オブジェクトか）
撤退時に消えないもの
```

**性能差は測れない、測れるのはガバナンスとIaC境界である** — これをレポートで先に宣言します。
測れないものを測ったふりをすると、レポート全体の信頼が落ちます。

---

## 2. 成果物の定義

リポジトリの主役は `docs/comparison/`。`src/` は計測装置。

### READMEの主張

> 同一のデータセットと同一SHAの学習コードを、5つのマネージドML基盤で
> 学習・登録・推論・撤退まで実行した。
> 精度比較ではなく、権限設計に要した試行回数、IaCで管理できる境界、
> 外部DBへの到達経路、撤退後の残留リソースを実測した。

### 最終転用先

```text
docs/comparison/selection-checklist.md
- 学習ジョブを最小権限で通すまでの典型的な障害と所要
- IaCで管理できる境界 / SDKに残る境界
- 外部システムへの接続に必要な設定の重さ
- 撤退時に残るリソースと手動削除が必要なもの
- アイドル時課金の構造差
```

---

## 3. Feature Store 不使用の明文化

**5基盤すべてでFeature Storeを使いません。**

```text
Vertex AI Feature Store        : 不使用
SageMaker Feature Store        : 不使用
Azure ML Feature Store         : 不使用
Databricks Feature Engineering
  (Unity Catalog feature table): 不使用
Snowflake Feature Store
  (snowflake-ml-python)        : 不使用
```

理由: California Housing には特徴量の更新もオンライン/オフライン整合性の問題も存在しない。
FSを呼んでも「機能を呼んだだけ」の記録しか残らず、比較軸としてもレポートとしても意味を持たない。

同じ理由で、ドリフト検知・モデル監視・バッチ/オンライン両系統も除外する。

**除外したことをREADMEに書く。** 何を作らなかったかは、作ったものと同じだけ情報量がある。

---

## 4. 共通ML仕様（全5基盤で同一）

```text
Data     : sklearn.datasets.fetch_california_housing
           取得後 Parquet 化して各基盤のストレージへ配置
Target   : MedHouseVal
Features : MedInc, HouseAge, AveRooms, AveBedrms,
           Population, AveOccup, Latitude, Longitude

Baseline : RandomForestRegressor
Main     : LightGBM Regressor       # 実務(LambdaRank)との連続性を優先
Metrics  : RMSE, MAE, R2
Seed     : 固定
```

精度は比較軸ではない。**同一SHAで同一メトリクスが再現されること**の確認用。
5基盤でRMSEが一致しない場合、それは基盤の差ではなく実装漏れなので、原因が判明するまで次へ進まない。

### パッケージ可用性の制約

```text
Snowflake warehouse Python : Snowflake Anaconda channel のパッケージに限定
                             → LightGBM は利用可能だがバージョン固定が効きにくい
Databricks                 : ML Runtime のプリインストール版と wheel 依存が衝突しうる
```

`src/common` は **依存を最小に保つ**（lightgbm / scikit-learn / pandas / pyarrow のみ）。
依存が増えるほど Tier B で崩れる。

---

## 5. 実行契約の差（設計の中心）

### Tier A: 学習側の契約差

```text
Vertex    : AIP_MODEL_DIR に成果物を書く / 引数は自由
SageMaker : /opt/ml/input/data/<channel>, /opt/ml/model, /opt/ml/output
            hyperparameters は /opt/ml/input/config/hyperparameters.json
Azure ML  : command job の outputs マウントパス / 引数は自由
```

解法: 共通CLI + プラットフォーム別シム。

```text
docker/training/
  Dockerfile                 # common deps + src/common (single image)
  entrypoint_vertex.sh
  entrypoint_sagemaker.sh
  entrypoint_azureml.sh
```

```bash
# all shims converge here
python -m common.train --input "$INPUT_DIR" --output "$MODEL_DIR" --params "$PARAMS_JSON"
```

### Tier A: 推論側は1イメージで3契約

```python
# src/serving/app.py -- one image, three platform contracts

@app.get("/health")        # Vertex (route is configurable)
@app.post("/predict")      # Vertex: {"instances": [...]} -> {"predictions": [...]}

@app.get("/ping")          # SageMaker: must return 200 on port 8080
@app.post("/invocations")  # SageMaker

@app.post("/score")        # Azure ML managed online endpoint
```

境界で形式変換のみ。中心の `predict()` は1つ。

### Tier B: 契約が「コンテナ」ではない

```text
Databricks
  投入   : Jobs API / Python wheel task（job cluster または serverless）
  追跡   : MLflow（基盤内蔵）
  登録   : Unity Catalog Models（catalog.schema.model の3階層名前空間）
  推論   : Mosaic AI Model Serving（scale-to-zero 可）
  同一性 : src/common を wheel 化して配布

Snowflake
  投入   : Snowpark Python stored procedure（warehouse実行）
  追跡   : 内蔵の実験追跡は限定的 → Neon側の ml_runs を一次記録とする
  登録   : Snowflake Model Registry（スキーマ内オブジェクト）
  推論   : Model Registry 経由の SQL 関数、または SPCS エンドポイント
  同一性 : src/common を stage へアップロードして import
```

**Snowflake で SPCS（Snowpark Container Services）を主経路にしない。**
SPCSを使えばコンテナ統一はできるが、それはSnowflakeの標準的な使い方ではなく、
「Snowflakeを別のKubernetesとして使った」記録にしかならない。
warehouse実行を主経路にし、SPCSは差分メモに留める。

### 統一単位の引き上げ

```text
v1: 同一コンテナイメージ  → Tier B で成立しない
v2: src/common の同一 git SHA → 5基盤すべてで成立
```

contract test で `code_revision` の一致を検証する。これが比較成立の唯一の担保。

---

## 6. Neon PostgreSQL への集約

### 位置付け

```text
Vertex AI ─────┐
SageMaker AI ──┤
Azure ML ──────┼──→ Neon PostgreSQL (pooled endpoint) ──→ SELECT で比較
Databricks ────┤
Snowflake ─────┘
```

**到達できるかどうか自体が比較軸。** Tier A は通常のegressで到達するが、
Tier B は宣言的な設定オブジェクトを経由する。この差を `failure_class = 'network'` として記録する。

### 接続方式

```text
書き込み用 : pooled endpoint (PgBouncer / transaction mode)
             ep-xxxx-pooler.<region>.<provider>.neon.tech
DDL/移行用 : direct endpoint
```

transaction pooling 使用時の注意（実装で踏む）:

```python
# src/telemetry/recorder.py
# Neon pooled endpoint runs PgBouncer in transaction mode.
# 1) Do not add a client-side pool on top of it.
# 2) Disable server-side prepared statements.
# 3) Neon compute may be suspended -> first connect can be slow; retry.

engine = create_async_engine(
    NEON_POOLED_URL,
    poolclass=NullPool,                       # PgBouncer owns pooling
    connect_args={"statement_cache_size": 0}, # asyncpg: no prepared statements
)
```

短命ジョブからの書き込みなので、**接続は開いて即閉じる**。
学習ジョブ本体とトランザクションを共有しない（学習失敗時にも記録が残る必要がある）。

### 到達不能時のフォールバック

```text
primary  : ジョブ内から Neon へ直接 INSERT
fallback : 結果を JSONL で各基盤のストレージへ出力
           → make collect でローカルから Neon へ流し込む
```

フォールバックを使った事実も記録する（`ml_runs.write_path = 'direct' | 'collected'`）。
どの基盤で直接書き込みが通らなかったかは、そのままレポートの一行になる。

### 着手前に確認が必要な点

```text
Snowflake : 外部への接続に必要な設定一式
            (network rule / secret / external access integration の構成と権限)
Databricks: ワークスペースのネットワーク構成で外部PostgreSQLへ到達できるか
            (serverless compute の egress 制約を含む)
```

いずれもプロバイダ側の仕様変更が入りやすい領域なので、
**Phase開始時に現行ドキュメントで確認する**。ここは推測で設計しない。

---

## 7. 計測スキーマ

```sql
create table ml_runs (
    run_id uuid primary key,
    platform text not null,        -- vertex | sagemaker | azureml | databricks | snowflake
    tier text not null,            -- A | B
    unification_unit text not null,-- container | package
    stage text not null,           -- train | register | deploy | predict
    status text not null,          -- success | failure
    attempt int not null default 1,
    duration_seconds double precision,
    failure_class text,            -- iam | quota | container | package | network | sdk | data | none
    error_excerpt text,
    code_revision text not null,   -- git sha of src/common
    write_path text not null,      -- direct | collected
    metrics jsonb,
    params jsonb,
    created_at timestamptz not null default now()
);

create table infra_events (
    event_id uuid primary key,
    platform text not null,
    action text not null,          -- apply | destroy
    duration_seconds double precision,
    resource_count int,
    residual_resources jsonb,      -- what survived destroy
    status text not null,
    created_at timestamptz not null default now()
);

create table cost_snapshots (
    platform text not null,
    usage_date date not null,
    service text not null,
    amount_usd numeric not null,
    primary key (platform, usage_date, service)
);
```

### 比較用SELECT

```sql
-- 1. metric parity check: all platforms must agree on the same SHA
select code_revision,
       platform,
       round((metrics->>'rmse')::numeric, 6) as rmse
from ml_runs
where stage = 'train' and status = 'success'
order by code_revision, platform;

-- 2. permission friction: how many fixes until the first successful training job
select platform,
       count(*) filter (where failure_class = 'iam')     as iam_fixes,
       count(*) filter (where failure_class = 'network') as network_fixes,
       count(*) filter (where failure_class = 'package') as package_fixes,
       max(attempt)                                      as attempts_to_success
from ml_runs
where stage = 'train'
group by platform
order by attempts_to_success desc;

-- 3. lifecycle duration by stage
select platform,
       stage,
       round(avg(duration_seconds)::numeric, 1) as avg_seconds
from ml_runs
where status = 'success'
group by platform, stage
order by platform, stage;

-- 4. teardown quality
select platform,
       avg(duration_seconds)                                  as avg_destroy_seconds,
       sum(jsonb_array_length(residual_resources))            as residual_total
from infra_events
where action = 'destroy'
group by platform;
```

**2番目のクエリが本命。** 「最小権限で学習ジョブが通るまでに何回直したか」は、
他のマルチクラウド比較にほとんど存在しない情報で、コンサル文脈で最も転用が効く。

---

## 8. Terraform 境界（5基盤）

| 基盤 | Terraform でカバー | SDK/CLI に残る |
|------|------------------|---------------|
| Vertex AI | IAM, GCS, Artifact Registry, Endpoint（器） | Custom Job, Experiment, Model Upload, Deploy |
| SageMaker AI | S3, ECR, IAM Role, Model Package Group, Model, Endpoint Config, Endpoint | Training Job, Model Registry 登録・承認 |
| Azure ML | Resource Group, Storage, ACR, Key Vault, App Insights, Workspace, Compute Cluster | Command Job, Model登録, Managed Online Endpoint 更新 |
| Databricks | Catalog, Schema, Grants, Cluster/Policy, Job, Registered Model, Model Serving Endpoint | ジョブ実行トリガ, MLflow run, モデルバージョン昇格 |
| Snowflake | Database, Schema, Warehouse, Role, Grants, Stage, Network Rule, Secret, External Access Integration | Stored Procedure 実行, Model Registry 登録, サービス関数作成 |

構造的な観察（レポートに書く仮説）:

```text
Databricks : 5基盤中もっとも Terraform 網羅度が広い
             （ガバナンスオブジェクトまでコード化できる）
Snowflake  : 器（DB/Schema/Role/Warehouse）は完全にコード化できるが、
             モデルレジストリ側はSQL/SDKに残る
Azure ML   : Workspace 単体でも周辺依存が多く、初期構築量が最大
Vertex AI  : ML固有リソースのTerraform対応が限定的
SageMaker  : Endpoint まで到達できるがモデルバージョン更新は分離が自然
```

共通原則: `terraform apply` に学習実行を含めない。
インフラ状態とML実行履歴が state 上で混ざると、両方の再現性が落ちる。

---

## 9. リポジトリ構成

```text
managed-ml-platform-comparison/
├── docs/
│   ├── comparison/
│   │   ├── 00_method.md                 # 比較の前提と測れないことの宣言
│   │   ├── 01_vertex.md
│   │   ├── 02_sagemaker.md
│   │   ├── 03_databricks.md
│   │   ├── 04_azureml.md
│   │   ├── 05_snowflake.md
│   │   ├── selection-checklist.md       # 主成果物
│   │   └── residual-resources.md
│   └── backlog/
│       ├── columnar-analytics.md        # pg_mooncake
│       └── feature-store-comparison.md  # 除外理由の記録
│
├── src/
│   ├── common/                          # 5基盤共通。ここのSHAが比較の担保
│   │   ├── dataset.py
│   │   ├── preprocess.py
│   │   ├── train.py
│   │   ├── evaluate.py
│   │   └── schemas.py
│   ├── serving/
│   │   └── app.py                       # Tier A: 1イメージ3契約
│   ├── telemetry/
│   │   └── recorder.py                  # Neon pooled endpoint writer
│   ├── vertex/
│   ├── sagemaker/
│   ├── azureml/
│   ├── databricks/                      # wheel build + Jobs API
│   └── snowflake/                       # stage upload + stored procedure
│
├── infra/
│   ├── modules/{gcp,aws,azure,databricks,snowflake,neon}/
│   └── environments/{gcp-dev,aws-dev,azure-dev,dbx-dev,sf-dev,neon-dev}/
│
├── docker/
│   └── training/
│       ├── Dockerfile
│       ├── entrypoint_vertex.sh
│       ├── entrypoint_sagemaker.sh
│       └── entrypoint_azureml.sh
│
├── scripts/
│   ├── check_residual.py                # 5基盤横断の残留リソース検出
│   └── collect_jsonl.py                 # fallback 収集
│
├── sql/
│   ├── schema.sql
│   └── comparison_queries.sql
│
├── tests/
│   ├── test_code_revision_parity.py
│   └── test_metric_parity.py            # 全基盤で RMSE が一致すること
├── Makefile
└── README.md
```

---

## 10. 実施順序

```text
Phase 0  ローカル基準実装             PMLE前
Phase 1  Vertex AI（アンカー）        PMLE前 / 2セッション / 既存資産流用
Phase 2  SageMaker AI                 PMLE後
Phase 3  Databricks                   Databricks ML Associate 学習と併走
Phase 4  Azure ML                     条件付き（Phase 3 完了時に go/no-go）
Phase 5  Snowflake                    SnowPro Core 着手をトリガとする
```

### Databricks を Azure より前に置く理由

```text
1. 次の資格が Databricks ML Associate であり、学習と実装が重なる
2. Terraform 網羅度が5基盤中もっとも広く、IaC比較の上限を先に確認できる
3. serverless model serving の scale-to-zero でアイドル課金リスクが低い
4. 既存の M-Wave10（Databricks adapter）と接続でき、成果が二重に効く
```

Azure は「周辺リソースが多くTerraform学習の手応えがある」という理由で価値はあるが、
比較軸としては Tier A 内の3つ目であり、限界効用が最も低い。

### 各Phaseの完了条件（全基盤共通）

```text
1. terraform apply
2. 学習ジョブ成功（失敗した試行も ml_runs に記録済み）
3. Neon へメトリクス到達（direct / collected のどちらかを記録）
4. モデル登録
5. 1件オンライン推論
6. terraform destroy
7. make check-residual で残留リソースを記録
8. docs/comparison/ に1ページ記述
```

**8 を次フェーズのブロック条件にする。**
Phase 1 完了時点で、レポート雛形の1列が埋まっている状態を作る。
評価軸が先に確定していないと、Phase 2以降で何を測るか毎回考え直すことになる。

---

## 11. コストと撤退

5基盤同時運用の最大リスクは、放置されたエンドポイントと計算リソース。

```text
Vertex Endpoint         : デプロイ中は常時課金
SageMaker Endpoint      : 常時課金（推論ゼロでも発生）
Azure Managed Endpoint  : 常時課金
Databricks Serving      : scale-to-zero 設定時はアイドル課金が小さい
                          job cluster の自動終了設定が必須
Snowflake Warehouse     : auto-suspend でアイドル課金が小さい
                          SPCS compute pool を使う場合はアイドル課金あり
```

### 予算

```text
Tier A 各基盤 : ¥2,000/月
Tier B 各基盤 : ¥1,000/月
合計上限     : ¥8,000/月
超過時       : Azure（Phase 4）を切り離す。5基盤は目的ではない
```

### 撤退スクリプト

`scripts/check_residual.py` を5基盤横断で作る。これ自体が転用可能な成果物になる。

```text
GCS バケット
ECR イメージ
Azure Key Vault の soft-delete
CloudWatch Logs グループ
Databricks: Unity Catalog に残る registered model / managed table
Snowflake : Time Travel / Fail-safe 期間中のデータ、stage 上の成果物
```

**「destroyしても消えないもの」の5基盤比較表** は、既存ポートフォリオに無い切り口。
Tier B は特に、データガバナンス機能（Time Travel、カタログ）が撤退を難しくする方向に働くはずで、
そこは仮説として先に書いておき、実測で確認する。

---

## 12. 破綻条件

| 条件 | 対応 |
|------|------|
| Phase 1 が2週間超 | 既存資産の流用に失敗。独立プロジェクト化をやめ既存リポジトリへ吸収 |
| 5基盤で RMSE が一致しない | 基盤の差ではなく実装漏れ。原因判明まで次へ進まない |
| `code_revision` が基盤間で不一致 | 比較不成立。contract test で自動検出 |
| Tier B で `src/common` がそのまま動かない | 依存を削って再統一。削れないなら「依存制約」を発見として記録し、その基盤を別枠にする |
| Snowflake から Neon へ到達できない | fallback（JSONL収集）に切替。到達不能自体を結果として記録する |
| 各Phase終了時にレポート1ページが未記述 | 事後に書けなくなる。次フェーズをブロック |
| 合計コストが ¥8,000/月 超過 | Azure を切り離す |
| Tier A で BYOC統一を諦める | 比較の前提が消える。統一不可なら発見として記録し比較軸から外す |

---

## 13. 着手前の確認事項

以下は仕様変更が入りやすく、推測で設計すると手戻りする。**各Phase開始時に現行ドキュメントで確認する。**

```text
Azure ML   : Workspace の必須依存リソース
             （App Insights / ACR が必須か任意かで初期構築量が大きく変わる）
Databricks : 無償枠・トライアルの範囲と期限
             serverless compute の外部ネットワーク到達性
             Terraform provider の Unity Catalog リソース対応範囲
Snowflake  : トライアルのクレジット額と有効期限（実質のタイムボックスになる）
             外部ネットワークアクセスに必要なオブジェクトと権限
             Model Registry の Terraform 対応範囲
Neon       : pooled endpoint のホスト名規則と接続上限
```

Snowflake のトライアル期限は、Phase 5 の実行可能期間を直接決める。
**Phase 5 は「まとめて一気にやる」前提で計画する**（他Phaseのような分散実行が効かない）。

---

## 14. 比較レポート 記入用テンプレート

| 判断軸 | Vertex AI | SageMaker AI | Azure ML | Databricks | Snowflake |
|--------|-----------|--------------|----------|------------|-----------|
| Tier | A | A | A | B | B |
| 統一単位 | container | container | container | package | package |
| 基盤作成の複雑性 | | | | | |
| Terraform でカバーできた範囲 | | | | | |
| SDK/SQL に残った範囲 | | | | | |
| 学習ジョブ成功までのIAM修正回数 | | | | | |
| 依存パッケージの制約 | | | | | |
| Neon への到達経路と設定の重さ | | | | | |
| エラーから原因到達までの時間 | | | | | |
| モデルの所在（レジストリ / カタログ） | | | | | |
| アイドル時課金の構造 | | | | | |
| apply / destroy 所要時間 | | | | | |
| destroy後の残留リソース | | | | | |

空欄は実測で埋める。埋まらない軸は、**埋まらなかった理由を書く**。
