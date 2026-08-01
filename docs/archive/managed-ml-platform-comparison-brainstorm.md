# マネージドML基盤 比較プロジェクト 設計方針（生ブレスト退避）

> **退避元**: `docs/01_requirements.md`（2026-07-31 に distill-spec で退避）
> **蒸留先**: 仕様 → `docs/01_requirements.md` / 基礎設計 → `docs/02_architecture.md`
> **権威**: この文書は archive であり権威順位は最下位（`docs/00_index.md` 参照）。確定定義は 01/02 が正本。

---

# マネージドML基盤 比較プロジェクト 設計方針

> リポジトリ名案: `managed-ml-platform-comparison`
> 題材: California Housing（データセットは題材ではなく **固定具 / fixture**）

---

## 0. 結論

原案の骨格（同一コード・3クラウド移植・Terraform境界の分離）は維持する。
変更点は次の5つ。

| # | 変更 | 理由 |
|---|------|------|
| 1 | 成果物をコードではなく「比較レポート＋選定チェックリスト」に定義し直す | 同一コードの3クラウド配置自体には希少性がない |
| 2 | 3クラウドを等しい深さで作らない | Vertexは既に踏破済み。学習予算はSageMaker/Azureへ配分 |
| 3 | コンテナ契約の差を設計の中心に置く | 原案が最も薄い箇所。ここが曖昧だと比較の前提が崩れる |
| 4 | 計測スキーマを run / infra_event / cost に分離し、失敗を一級データにする | 成功のみ記録すると最も価値ある情報が消える |
| 5 | `pg_mooncake` フェーズを削除（backlog行き） | 合成ログでは現実のクエリパターンが無く、結論が使えない |

---

## 1. 成果物の再定義

コード自体は証拠にならない。同一の `train.py` を3クラウドに置くだけでは、生成物としての希少性が低い。
希少なのは **実測値と、そこから導いた選定判断** である。

したがってリポジトリの主役は `docs/comparison/` であり、`src/` は **計測装置** という位置付けにする。

### READMEの主張（実行事実ではなく判断で書く）

> 同一の学習コード・同一コンテナを3つのマネージドML基盤で動かし、
> 権限設計に要した試行回数、IaCで管理できる境界、撤退手順と残留リソースを実測した。
> 精度比較ではなく、**選定時に効く差分の文書化**を目的とする。

### 最終的な転用先

```text
docs/comparison/selection-checklist.md
- 学習ジョブを最小権限で通すまでの典型的な障害と所要
- IaCで管理できる境界 / SDKに残る境界
- 撤退時に残るリソースと手動削除が必要なもの
- エンドポイント常時課金の構造差
```

これは実装の証拠ではなく、**何を先に潰すかを決められる立場にいることの証拠**になる。

---

## 2. 深さを意図的に非対称にする

既存の `study-hybrid-search-vertex` で Custom Training / Model Registry / Endpoint / Pipelines は踏破済み。
Phase 1 は学習ではなく **既存資産の移植** であり、短く終わらせるのが正しい配分。

```text
Phase 1 (Vertex)   : アンカー。2セッション。
                     既存の make ターゲット / destroy-all / contract test を流用
Phase 2 (SageMaker): 差分測定。ここに学習予算を集中
Phase 3 (Azure ML) : 条件付き。Phase 2完了時に go/no-go 判定
```

### Pipelines は Phase 2/3 で実装しない

パイプライン機能の比較は「機能を呼んだだけ」になりやすい。
差は次の4点で十分に出る。

```text
学習ジョブ投入 / モデルレジストリ / エンドポイント / IAM
```

---

## 3. コンテナ契約の統一（原案の最大の穴）

「同一コンテナ」を主張するなら、3基盤の入出力契約の差を正面から扱う必要がある。

### 学習側の契約差

```text
Vertex    : AIP_MODEL_DIR に成果物を書く / 引数は自由
SageMaker : /opt/ml/input/data/<channel>, /opt/ml/model, /opt/ml/output
            hyperparameters は /opt/ml/input/config/hyperparameters.json
Azure ML  : command job の outputs マウントパス / 引数は自由
```

### 解法: 共通CLI + プラットフォーム別シム

```text
docker/training/
  Dockerfile              # common deps + src/common (single image)
  entrypoint_vertex.sh
  entrypoint_sagemaker.sh
  entrypoint_azureml.sh
```

各シムは最終的に同じコマンドへ落とす。

```bash
python -m common.train --input "$INPUT_DIR" --output "$MODEL_DIR" --params "$PARAMS_JSON"
```

### 推論側は1つのFastAPIアプリで3契約を同時に満たす

```python
# src/serving/app.py -- one image, three platform contracts

@app.get("/health")        # Vertex (route is configurable)
@app.post("/predict")      # Vertex: {"instances": [...]} -> {"predictions": [...]}

@app.get("/ping")          # SageMaker: must return 200 on port 8080
@app.post("/invocations")  # SageMaker

@app.post("/score")        # Azure ML managed online endpoint
```

境界でリクエスト形式だけ変換し、中心の `predict()` は1つにする。
これは説明可能な小さい成果物としても機能する。

### BYOC統一を前提条件にする

SageMaker は prebuilt container + script mode が最短だが、それを使うとコード同一性が崩れる。
**3クラウドとも BYOC で揃える**。
この制約により SageMaker がチュートリアルより重くなる事実自体が、レポートの発見になる。

---

## 4. 計測スキーマ

原案の `ml_runs` は run単位 と environment単位 の情報が混在しており NULL が増える。
またコストは実行時に確定しない（3クラウドとも請求反映に1〜2日の遅延がある）。よって分離する。

```sql
create table ml_runs (
    run_id uuid primary key,
    cloud text not null,
    stage text not null,          -- train | register | deploy | predict
    status text not null,         -- success | failure
    attempt int not null default 1,
    duration_seconds double precision,
    failure_class text,           -- iam | quota | container | sdk | data | none
    error_excerpt text,
    code_revision text not null,  -- git sha of src/common
    metrics jsonb,
    params jsonb,
    created_at timestamptz not null default now()
);

create table infra_events (
    event_id uuid primary key,
    cloud text not null,
    action text not null,         -- apply | destroy
    duration_seconds double precision,
    resource_count int,
    residual_resources jsonb,     -- what survived destroy
    status text not null,
    created_at timestamptz not null default now()
);

create table cost_snapshots (
    cloud text not null,
    usage_date date not null,
    service text not null,
    amount_usd numeric not null,
    primary key (cloud, usage_date, service)
);
```

### 核心は `failure_class` と `attempt`

```sql
-- how many permission fixes until first successful training job
select cloud, count(*) as iam_fixes
from ml_runs
where stage = 'train' and failure_class = 'iam'
group by cloud;
```

**「最小権限で学習ジョブが通るまでに何回権限を直したか」**
**「エラーメッセージから原因に到達するまでの時間」**

これらは他のマルチクラウド比較記事にほとんど存在しない。
コンサル文脈で最も転用が効くのもここ。成功だけを記録するとこの情報は消える。

### 比較成立の担保

`code_revision` を必須にし、3クラウドの run が同一SHAであることを contract test で検証する。
これが比較成立の唯一の担保。

---

## 5. 削除するもの

### pg_mooncake フェーズ

6,000万件の合成予測ログを作って列指向の優位性を測っても、現実のクエリパターンが無いため結論が使えない。
作った問題に自分で答える構造になる。

```text
docs/backlog/columnar-analytics.md
trigger: 実案件で Neon / 分析基盤の判断が必要になったとき
```

### Neonの残し方

`ml_runs` / `infra_events` / `cost_snapshots` の保存先として残す。数千行なので通常のPostgreSQLで十分。
**Sakura VPS 置換の検討とは別プロジェクトとして扱う**（混ぜると両方の判断が濁る）。

### Feature Store / ドリフト検知 / 監視

原案の破綻条件の指摘どおり除外。
California Housing では特徴量更新が存在せず、呼び出すだけになる。

---

## 6. コストと撤退の設計

3クラウド同時運用で最も現実的なリスクは、放置されたエンドポイント。
SageMaker と Azure のマネージドエンドポイントは、推論していなくても課金が継続する。

```text
各クラウド予算アラート : ¥2,000/月
合計上限             : ¥6,000/月（超過したら Azure を切り離す）
セッション終了処理     : make destroy-all → make check-residual
```

### `check-residual` を横断スクリプトとして作る

destroy後に残るものを列挙し、`infra_events.residual_resources` に書き込む。

```text
GCS バケット
ECR イメージ
Azure Key Vault の soft-delete
CloudWatch Logs グループ
```

**「destroyしても消えないもの」の3クラウド比較表**は、既存ポートフォリオに無い切り口。

---

## 7. リポジトリ構成（修正版）

```text
managed-ml-platform-comparison/
├── docs/
│   ├── comparison/
│   │   ├── 01_vertex.md
│   │   ├── 02_sagemaker.md
│   │   ├── 03_azureml.md
│   │   ├── selection-checklist.md      <- 主成果物
│   │   └── residual-resources.md
│   └── backlog/
│       └── columnar-analytics.md
│
├── src/
│   ├── common/                          <- 3クラウド共通。ここのSHAが比較の担保
│   │   ├── dataset.py
│   │   ├── preprocess.py
│   │   ├── train.py
│   │   ├── evaluate.py
│   │   └── schemas.py
│   ├── serving/
│   │   └── app.py                       <- 1アプリで3契約
│   ├── telemetry/
│   │   └── recorder.py                  <- ml_runs / infra_events への書き込み
│   ├── vertex/
│   ├── sagemaker/
│   └── azureml/
│
├── infra/
│   ├── modules/{gcp,aws,azure,neon}/
│   └── environments/{gcp-dev,aws-dev,azure-dev,neon-dev}/
│
├── docker/
│   └── training/
│       ├── Dockerfile
│       ├── entrypoint_vertex.sh
│       ├── entrypoint_sagemaker.sh
│       └── entrypoint_azureml.sh
│
├── scripts/
│   └── check_residual.py
│
├── sql/
│   └── schema.sql
│
├── tests/
│   └── test_code_revision_parity.py     <- contract test
├── Makefile
└── README.md
```

---

## 8. Terraform境界（原案を踏襲）

| 区分 | 担当 |
|------|------|
| 静的基盤（IAM / Storage / Registry / Workspace / Endpoint基盤 / Neon） | Terraform |
| 学習ジョブ実行 / モデル登録 / デプロイ | Python SDK・CLI |

理由: `terraform apply` に学習を含めると、インフラ状態とML実行履歴が state 上で混ざる。

Vertex AI は Terraform 対応が限定的なため、次の境界が現実的。

```text
Terraform  : IAM, Bucket, Artifact Registry, Endpoint
Python SDK : Custom Job, Experiment, Model Upload, Deploy
```

SageMaker は Endpoint まで Terraform で管理可能だが、モデルバージョン更新は
`Training Job → Model Registry → 承認 → Deployスクリプト` に寄せる。

---

## 9. 共通ML仕様

```text
Data     : sklearn.datasets.fetch_california_housing
Target   : MedHouseVal
Features : MedInc, HouseAge, AveRooms, AveBedrms,
           Population, AveOccup, Latitude, Longitude

Baseline : RandomForestRegressor
Main     : LightGBM Regressor       <- 実務(LambdaRank)との連続性を優先
Metrics  : RMSE, MAE, R2
```

精度は比較軸ではない。**同一SHAで同一メトリクスが再現されること** の確認用。

---

## 10. 実施順序（PMLEとの関係を含む）

```text
PMLE受験前 : Phase 0（ローカル基準実装）
             Phase 1（Vertex移植、2セッション）
PMLE受験後 : Phase 2（SageMaker）
判定後     : Phase 3（Azure、go/no-go で決定）
```

Phase 1 の Vertex 部分は PMLE 出題範囲と重なるが、既に実装済みの領域のため新規学習にはならない。
Phase 2 以降は試験と無関係。

### 各Phaseの完了条件（共通）

```text
1. terraform apply
2. 学習ジョブ成功（失敗した試行も ml_runs に記録済み）
3. 評価指標を記録
4. モデル登録
5. 1件オンライン推論
6. terraform destroy
7. make check-residual で残留リソースを記録
8. docs/comparison/ に1ページ記述
```

**8 を次フェーズのブロック条件にする。** Phase 1 完了時点でレポート雛形（1クラウド分）が埋まっている状態を作る。
評価軸が先に確定していないと、Phase 2 で何を測るか毎回考え直すことになる。

---

## 11. 破綻条件

| 条件 | 対応 |
|------|------|
| Phase 1 が2週間超 | 既存資産の流用に失敗。独立プロジェクト化をやめ、既存リポジトリへ吸収 |
| `code_revision` が3クラウドで一致しない | 比較不成立。contract test で自動検出 |
| 各Phase終了時にレポート1ページが未記述 | 事後に書けなくなる。次フェーズをブロック |
| 合計コストが ¥6,000/月 を超過 | Azure を切り離す。3クラウドは目的ではない |
| BYOC統一を諦め SageMaker で script mode を使う | 比較の前提が消える。統一不可なら、その事実を発見として記録し比較軸から外す |

---

## 12. 着手前の確認事項

Azure ML Workspace の必須依存リソースは azurerm プロバイダのバージョンによって変わる。
Application Insights と Container Registry が必須か任意かで初期構築量が大きく違うため、
**Phase 3 の見積り前に現行版のドキュメントで確認する。**

---

## 13. 比較レポートの軸（記入用テンプレート）

| 判断軸 | Vertex AI | SageMaker AI | Azure ML |
|--------|-----------|--------------|----------|
| 基盤作成の複雑性 | | | |
| Terraform でカバーできた範囲 | | | |
| SDKに残った範囲 | | | |
| 学習ジョブ成功までのIAM修正回数 | | | |
| エラーから原因到達までの時間 | | | |
| コンテナ契約の制約 | | | |
| destroy後の残留リソース | | | |
| エンドポイント常時課金の構造 | | | |
| apply / destroy 所要時間 | | | |

空欄は実測で埋める。埋まらない軸は、埋まらなかった理由を書く。
