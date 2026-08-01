# 動作検証: Databricks（Phase 3）

| | |
|---|---|
| Tier | B（データ基盤内蔵型・統一単位 = **wheel**。コンテナイメージが登場しない） |
| ENV / PLATFORM | `dbx-dev` / `databricks` |
| 資格情報 | `DATABRICKS_HOST` / `DATABRICKS_TOKEN`（SDK が env から拾う）。[credentials.md §6](./credentials.md) |
| 実装 | `src/platforms/databricks/adapter.py` / `src/platforms/databricks/job_main.py` |
| 着手前 | databricks-phase-precheck.md を消化してから |

> ## ✅ 完走（2026-08-01）— 8項目すべて達成
>
> RMSE `0.4368055090296257` / 予測値 `4.183217948107466`（**5基盤すべてで一致**）/
> 到達経路 **collected**（psycopg 不在。仮説どおり）/ **5基盤で唯一の残留ゼロ**（FAIL・WARN とも 0 件）。
> 結果は [comparison/03_databricks.md](../comparison/03_databricks.md)。
>
> ただし `failure_class=sdk` が **6件で5基盤中最多**、到達までの試行回数も多い
> （train attempt 3 / register attempt 3 / deploy attempt 3）。
> **Free Edition で踏む地雷は8つ**（§0-c）。順に潰せば ①→⑦ を2〜3時間で通せる。

共通の前提・8項目の定義・停止条件は [README.md](./README.md)。以下は Databricks 固有分のみ。

**Tier A との最大の違いは、ここから手順の形が変わること。** イメージを push せず wheel を
Volume へ置き、ジョブ定義は Terraform が持ち、adapter は起動しかしない。

## 0-a. Free Edition で ML はできるか（2026-08-01 調査・結論: **できる。ただし条件付き**）

出典: [Free Edition limitations](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations)（doc 更新 2026-07-20 / 参照 2026-08-01）。
**「使えない」ではなく「枠が小さい」型の制限**で、Snowflake トライアル（機能そのものが無効）とは質が違う。

| 完了条件 | Free Edition での可否 | 根拠 |
|---|---|---|
| ② 学習ジョブ | ✅ serverless のみ・**同時実行5タスクまで** | Compute limitations |
| ③ Neon 到達 | ⚠️ **egress が trusted domains に限定** | 「outbound internet access is restricted to a limited set of trusted domains」 |
| ④ UC 登録 | ✅ 制限記載なし（メタストアは1アカウント1個） | Administrative limitations |
| ⑤ 推論 | ✅ **CPU のカスタムモデルなら可**。GPU・provisioned throughput・バッチ推論は不可、**エンドポイント数に上限** | Model serving endpoints |
| 認証 | ❌ アカウントコンソール不可 = **OAuth SP を作れず PAT 一本** | 同上 |

**踏むと痛い順に3つ。**

1. **egress 制限は Snowflake の再演になりうる。** ジョブ起動時に wheel の依存
   （lightgbm / scikit-learn / pandas / pyarrow）を PyPI から入れる。PyPI は
   trusted domains に含まれる想定だが、**②で実測して確かめる**。落ちたら
   `failure_class='network'`（今度こそ正しい分類）で記録し、`extra_dependencies` ではなく
   **事実を先に残す**。
2. **`Verify identity`（LinkedIn 認証）で「Outbound internet access」が解放される。**
   ワークスペース右上のボタン。つまり **③ の結果がアカウントの認証状態に依存する**。
   認証前に測れば `collected`、認証後なら `direct` の可能性がある。
   **どちらの状態で測ったかを comparison ページに必ず書く**（書かないと再現できない）。
3. **fair usage を超えるとその日（最悪その月）compute が止まる。** 失敗リトライが
   そのまま残り時間を削る。①→⑤ を一気に通す前提で、plan と wheel を先に固める。

非商用限定・SLA なし。本ラボは個人学習なので条件は満たす。

## 0-b. 着手前ブロッカー（2026-08-01 の準備で判明・**未解決**）

先に潰さないと ①・④ で確実に止まる。実測の根拠は
databricks-phase-precheck.md「実測」。

### B-1. 資格情報 —— ✅ **解決済み（2026-08-01）**

`DATABRICKS_HOST` / `DATABRICKS_TOKEN` を Doppler に登録し疎通確認済み
（`mcml-lab` / all-apis / **期限 2026-10-30**。詳細は [credentials.md §6](./credentials.md)）。

```bash
doppler run -- .venv/bin/python -c \
  "from databricks.sdk import WorkspaceClient as W; print(W().current_user.me().user_name)"
```

**PAT をファイルで受け渡した場合は、登録後に必ずファイルを消す**
（今回 `aaa` を `shred -u` で削除済み。リポジトリ直下に置いたままにしない）。

### B-2. ④ 登録の経路 —— ✅ **解決済み（2026-08-01・案A で実装）**

`w.model_versions` に `create` が無く（実測）、公式も
「Creating new model versions requires use of the MLflow Python client」と明記する。
**UC の版は MLflow クライアント経由でしか作れず、model signature も必須**。

採った方式（案A）: **登録も Terraform 定義のジョブを `--stage register` で起こして行う**。
`job_main.register_model()` が Volume 上の `model.txt` を読み、
`mlflow.lightgbm.log_model(..., input_example=..., registered_model_name=...)` で UC へ登録する。
adapter は起動と待機だけを行い、**版番号は UC を引き直して確定**させる（stdout を信用しない）。

- mlflow は wheel の依存に入れない（serverless にプリインストール）
- ④ の所要には**ジョブ起動のオーバーヘッドが乗る**（実測 55.2s）。実装都合ではなく
  基盤の構造なので、そのまま比較表に載せる

## 0-c. Free Edition で踏んだ地雷（2026-08-01 実測・**すべて一次記録**）

| # | 症状 | 原因 | 対処 |
|---|---|---|---|
| 1 | `cannot create catalog: Metastore storage root URL does not exist` | Free Edition は **Default Storage**。メタストアに storage root が無く、API 経由の `CREATE CATALOG` は MANAGED LOCATION を要求する（**SDK でも同じエラー = provider のバグではない**） | `env/config.yaml` の `terraform.dbx-dev` で `create_catalog: false` + `catalog_name: workspace`。**`schema_name` にラボ接頭辞を入れる**（カタログ名で絞れなくなるため） |
| 2 | `ERROR_UNSUPPORTED_PYTHON_VERSION`（wheel の install 失敗） | serverless `environment_version=2` は **Python 3.11**。本ラボの wheel は `requires-python >=3.12` | `environment_version = 3`（3〜5 が 3.12）。module 既定を変更済み |
| 3 | 学習が例外で落ちたのに **task が SUCCESS** | `python_wheel_task` は entry point を**関数として呼ぶ**ので `return 1` が握り潰される | console script を `cli()` にし **SystemExit で返す** |
| 4 | 学習成功なのに `Workload failed` / `error: SystemExit: 0` | **`SystemExit(0)` は失敗扱い** | 成功時は何も送出しない |
| 5 | `OSError: [Errno 29] Illegal seek`（`model.txt` だけ在って metrics が無い） | **UC Volume は FUSE で seek 不可**。`to_csv` と JSONL の append が落ちる | 学習も記録も**ローカルへ書いてから Volume へコピー**（`copy_to_volume`） |
| 6 | 登録が `RESOURCE_DOES_NOT_EXIST: No experiment was found` | wheel task には**既定の MLflow 実験が無い**（notebook と違う） | `--experiment /Users/<user>/mcml-lab` を渡す |
| 7 | `EndpointCoreConfigInput.__init__() missing 1 required positional argument: 'name'` | SDK 0.123 は config 側にも name 必須 | `name=` を渡す |
| 8 | `ResourceConflict: Endpoint served entities are currently being updated` | 作成中に deploy を再実行した | **IN_PROGRESS 中は触らない**。`state.ready` を見て待つ |

**PyPI へは届いた**（trusted domains に含まれている）。Snowflake トライアルで
登録を止めた「外部リポジトリ不可」は Databricks Free Edition では**起きない**。
egress 制限は Neon（任意の外部 PostgreSQL）側に効く。

## 1. 着手前チェック

```bash
doppler run -- databricks current-user me       # host / token の疎通
make PLATFORM=databricks deps-platform          # databricks-sdk + build（wheel 生成）
make test
```

- [ ] **`make PLATFORM=databricks deps-platform` 済み**。`make deps` は基盤 SDK を入れない（学習コンテナを太らせないため基盤ごとに別 extra）
- [ ] `DATABRICKS_HOST` / `DATABRICKS_TOKEN` が Doppler にある（この2件だけ。
      ワークスペースは Databricks 側が持つので **AWS の資格情報は要らない**）
      → **未登録。§0-b B-1 の手順で発行する**
- [ ] **PAT の有効期限を記録した**。Free Edition では SP を発行できないため PAT が唯一の経路で、
      この期限が Phase 3 の実行可能期間を決める
- [ ] precheck 8項目に結論が出ている。特に **項目6（Free Edition で Model Serving を
      作れるか）** —— 作れなければ完了条件⑤が成立せず go/no-go の対象になる
- [ ] Phase 2 の comparison ページが書き終わっている

## 2. ① terraform apply

**実際に通った手順（2026-08-01 / Free Edition）。** 変数を4つとも渡さないと ① で落ちる。

```bash
# 変数の export は不要。create_catalog / catalog_name / schema_name は
# env/config.yaml の terraform.dbx-dev 節、job_principal は Doppler
# （MCML_TF_DBX_JOB_PRINCIPAL）から入る。未解決なら apply 前に名前を挙げて落ちる。

make ENV=dbx-dev tf-init
doppler run -- terraform -chdir=infra/environments/dbx-dev plan -out=dbx.tfplan
doppler run -- .venv/bin/python scripts/run_terraform.py apply --env dbx-dev dbx.tfplan
doppler run -- terraform -chdir=infra/environments/dbx-dev output -json \
  > artifacts/dbx-dev.outputs.json
```

- **plan も `doppler run --` で回す**。state に既存リソースがあると refresh に認証が要り、
  素の `terraform plan` は `cannot configure default credentials` で落ちる。
- **outputs は destroy の前に保存する**。残留検査が catalog / schema をここから引く。
- `schema_name` にラボ接頭辞を入れるのは、カタログ名で絞れなくなるため
  （残留検査の `LAB_NAME_PREFIX` がスキーマ名にしか掛からない）。

Terraform が持つもの: Catalog / Schema / Grants / Cluster・Policy / **Job 定義** /
Registered Model / Serving Endpoint。
`config.py` が読む outputs: `catalog_name` / `schema_name` / `serving_endpoint_name`。

**Free Edition では apply が部分的に落ちうる**（precheck 項目5〜7）。
`databricks_cluster_policy` が拒否されたら `count` で外す（ジョブは serverless なので実害なし）。
`CREATE CATALOG` が通らなければ `terraform.dbx-dev.create_catalog: false` で既存カタログを使う。
**回避策を積む前に、落ちた事実を `infra_events` と comparison ページに残す。**

**ジョブ定義を adapter 側で `jobs/create` しない。** 「Terraform でどこまで書けたか」が
比較軸そのものなので、SDK で作り直すと Databricks だけ網羅度が水増しされる。

### tfstate の初期化（Tier B は Neon backend）

**Tier B は自前で tfstate を置けない。** UC Volume も Snowflake stage も Terraform
backend ではなく、オブジェクトストレージが外に無い。以前は GCP のバケットへ
相乗りしていたが、**GCP を畳むと Tier B 2基盤の state を失う**ため、
比較対象に含まれない Neon（計測 DB）を中立の置き場にした。

```bash
doppler run --command 'terraform -chdir=infra/environments/dbx-dev init \
  $(.venv/bin/python scripts/tf_backend.py dbx-dev)'
```

⚠️ **接続文字列を手で組まない。** Neon 固有の要件が2つあり、どちらも外すと init が落ちる:

| 要件 | 理由 |
|---|---|
| **direct endpoint**（pooled ではない） | pg backend の state lock はセッションレベル advisory lock。transaction pooling では動かない |
| **`options=endpoint=<id>`** | pg backend の `lib/pq` が SNI 非対応。素の Neon URI は `Endpoint ID is not specified` で落ちる（2026-08-01 実測） |

state は環境ごとに別スキーマ（`tfstate_dbx_dev` / `tfstate_sf_dev`）へ入る。

### apply の実行（5基盤共通）

**Terraform 変数は `export` しない。** `env/config.yaml` の `terraform:` 節（人が決める値）と
Doppler（秘密・個人識別子）から `run_terraform.py` が組み立てる（対応表は
`src/platforms/terraform_vars.py`）。解決できない変数があれば **terraform を起動する前に**
名前を挙げて落ちる。

対話端末があるなら:

```bash
make ENV=dbx-dev tf-init && make ENV=dbx-dev tf-plan && make ENV=dbx-dev tf-apply   # yes を入力
```

**非対話（CI・バックグラウンド・エージェント）は保存済み plan を渡す。**
素の `tf-apply` は承認プロンプトが `EOF` で落ちるうえ、**`infra_events` に偽の
failure 行が残って「apply 試行回数」を汚す**（2026-08-01 に実際に発生）。
plan 無しで非対話実行するとガードが `EXIT_USAGE` で止める:

```bash
PYTHONPATH=src doppler run -- .venv/bin/python scripts/run_terraform.py \
  plan --env dbx-dev -out=/tmp/dbx-dev.tfplan
PYTHONPATH=src doppler run -- .venv/bin/python scripts/run_terraform.py \
  apply --env dbx-dev /tmp/dbx-dev.tfplan
```

⚠️ **plan も `run_terraform.py` 経由で回す。** 素の `terraform plan` は
config.yaml / Doppler 由来の `-var` を受け取らないため、**変数の抜けた plan が
保存され、apply がそれを適用してしまう**（2026-08-01 実測: 予算アラートが落ちて
`Plan: 9 to add`。正しくは 10）。plan は `infra_events` に記録しない。

destroy も同じ（`terraform destroy <plan>` は terraform 自身が拒否するので
`plan -destroy` の成果物を渡す。記録上の action は destroy のまま保たれる）:

```bash
PYTHONPATH=src doppler run -- .venv/bin/python scripts/run_terraform.py \
  plan --env dbx-dev -destroy -out=/tmp/dbx-dev-destroy.tfplan
PYTHONPATH=src doppler run -- .venv/bin/python scripts/run_terraform.py \
  destroy --env dbx-dev /tmp/dbx-dev-destroy.tfplan
```

## 3. 配布物の準備（wheel）

```bash
make wheel        # stamp-revision に依存。stamp 忘れはビルド前に止まる
```

生成物は `artifacts/dist/multicloud_ml_platform_lab-0.1.0-py3-none-any.whl`。
**wheel と学習データの両方**を Volume へ置く（データが無いとジョブは exit 2 で即死する）。

```bash
make dbx-upload      # wheel をビルドし直し、wheel + data/california_housing.parquet を Volume へ
```

**コードを直したら必ず `make dbx-upload` をやり直す。** ジョブは Volume 上の wheel を
読むので、ローカルを直しただけでは**前の wheel が動き続ける**（同じ失敗を再現して混乱する）。

- [x] dist 名が4箇所で一致（Terraform `job_package_name` / `wheel_path` /
      `env/config.yaml` の `wheel_filename` / 実 dist 名）。`tests/test_packaging_contract.py` が pin
- [x] entry point が `train = platforms.databricks.job_main:cli`（**`main` ではない**。戻り値ではジョブが落ちない）
      （`unzip -p <whl> '*/entry_points.txt'` で確認）
- [x] `core/ml/config/_stamp.py` が wheel に同梱されている

**2026-08-01 にビルドして中身まで確認済み**（上記3点）。wheel の実行時依存は
lightgbm / scikit-learn / pandas / pyarrow のみで、**psycopg は入らない**。
egress を測る以前に Neon 直 INSERT の手段が無いので、③ が `collected` になるのは確定。

**wheel の中に .git も `CODE_REVISION` も無い。** 焼き忘れるとジョブ起動後に
`CodeRevisionError` で落ち、実行時間を捨てた上に原因が「Databricks の問題」に見える。

## 4. ②〜⑤ フェーズ実行

```bash
make PLATFORM=databricks phase-train          # ② 55s 前後（ジョブ内学習は 8.5s）

# ③' JSONL を Volume から回収して Neon へ（**ファイル名は ml_runs.jsonl のまま**置く。
#     collect_jsonl は rglob('ml_runs.jsonl') で拾うので、改名すると無視される）
#     回収スクリプトは runbook 末尾のスニペット参照
make collect COLLECT_DIR=artifacts/fallback/databricks

# ④⑤ は成果物 URI / 版を明示して再開できる（前段をやり直さない）
doppler run -- .venv/bin/python scripts/run_phase.py databricks register \
  --artifact-uri /Volumes/<catalog>/<schema>/artifacts/runs/<run_id>/model
doppler run -- .venv/bin/python scripts/run_phase.py databricks deploy --model-version <n>
make PLATFORM=databricks phase-predict
```

**⑤ は 8分前後かかる**（毎回コンテナをビルドする）。`run_phase` は完了まで待つので、
2分で切られる実行環境では**バックグラウンドに回す**こと。
**作成中に再実行しない**（`ResourceConflict`。IN_PROGRESS 中は触れない）。

| # | 何を見て成功と判定するか |
|---|---|
| ② | Job run の `result_state = SUCCESS`。`ml_runs` に stage=train。**成功行は job_main（ジョブ側）が書く**。投入自体が失敗した行だけ adapter が書く |
| ③ | **`write_path='collected'` が現状の想定**（下記）。`direct` が出たら仮説が外れたということなので、そちらを発見として記録する |
| ④ | UC に `catalog.schema.california_housing` の版ができる（3階層名前空間）。**現状は実行不可**（§0-b B-2） |
| ⑤ | serving の query が predictions を返す。payload は `dataframe_records` |

### ③ の扱い（この基盤の主要観測点）

serverless ジョブへ Neon 接続情報を渡す経路が**まだ配線されていない**
（Terraform の environment.spec に env が無く、Databricks secret 参照も未配線）。
そのためジョブ側は Volume 上の JSONL fallback に落ち、`write_path='collected'` になる。

これは不具合として黙って直す対象ではなく、**Tier B の到達経路の比較データ**。
配線を試みるなら precheck の項目として実施し、試行回数と失敗理由を `ml_runs` に残す。

## 5. Databricks 固有の確認

- **job ID は名前から引く**（`job_name = mcml_dev_train`）。ID をハードコードしない。
- **python_params は Tier A のシムと同じ形**（`--input` / `--output` / `--params` /
  `--run-id` / `--attempt`）。`--input` / `--output` が無いと CLI が exit 2 で即死する。
- **`scale_to_zero_enabled` を必ず立てる**。Tier A の常時課金との構造差がアイドル課金比較の核心で、
  落とすと比較の前提が変わる（`env/config.yaml` で `true` 固定）。
- **推論が自前コンテナを経由しない**。基盤が直接モデルを配信するので、
  `docker/serving` の3契約はこの基盤では使われない。
- ML Runtime のプリインストール版と wheel の依存が衝突しうる（依存を最小にしてある理由）。

## 6. 失敗時の切り分け

| failure_class | 典型 | 対処 |
|---|---|---|
| `permission` | UC の catalog / schema / volume への grant 不足 | grant を1つずつ。Tier A の IAM と試行回数を比べるのが目的 |
| `network` | serverless egress から Neon へ届かない | **想定内**。JSONL fallback を確認し記録 |
| `container` | wheel の entry point を引けない / 依存衝突 | dist 名4箇所の一致と `entry_points.txt` |
| `data` | Volume 上の入力パス違い | `/Volumes/<catalog>/<schema>/artifacts/` 配下を確認 |

## 7. ⑥⑦ teardown と残留検査

```bash
make PLATFORM=databricks phase-teardown   # serving エンドポイント + **UC のモデル版**を削除

# 変数は config.yaml / Doppler から入る
       TF_VAR_catalog_name=workspace TF_VAR_schema_name=mcml_dev
doppler run -- terraform -chdir=infra/environments/dbx-dev plan -destroy -out=dbx-destroy.tfplan
doppler run -- .venv/bin/python scripts/run_terraform.py destroy --env dbx-dev dbx-destroy.tfplan
doppler run -- .venv/bin/python scripts/check_residual.py --platform databricks --record
```

scale-to-zero でも**エンドポイント定義自体は残る**ので teardown は必須。

**teardown は UC のモデル版まで消す。** 残すと destroy が
`cannot delete registered model: ... has 2 model versions(s)` で止まる
（`databricks_registered_model` に force_destroy が無い）。
**Vertex は登録モデルが残っても destroy が通る**ので、ここは Tier B 固有。

保存済み destroy plan は `terraform destroy <plan>` では適用できない
（terraform 自身が拒否する）。`run_terraform.py` が apply に読み替えて
**記録上の action は destroy のまま**にする。

| kind | severity | **実測（2026-08-01）** |
|---|---|---|
| `serving_endpoint` | FAIL | **0件** |
| `uc_registered_model` | WARN | **0件**（teardown で版を消し、器は destroy が消す） |
| `uc_volume` | WARN | **0件**（schema ごと destroy されるので wheel も消える） |

**残留ゼロ。** Snowflake の `fail_safe` のような「消せない固定行」も無い。

**旧「既知の穴」は 2026-08-01 に修正済み。** volume 列挙が
`volumes.list(catalog_name="", schema_name="")` で実クラウドでは一度も成立しなかった
（テストは注入クライアントで通っていた）。現在は
`artifacts/dbx-dev.outputs.json` の catalog / schema で引き、

- outputs が無い → **ERROR**（検査できなかったことを残す。黙って0件にしない）
- カタログごと消えている → **残留ゼロ**（destroy 成功を ERROR に見せない）

に分けてある。**destroy の前に outputs を保存しておくこと**（§2 のコマンド）。
残留は本ラボの主要な比較軸なので、`uc_volume` が ERROR のまま「残留なし」と書かない。

## 8. ⑧ レポート記述

[docs/comparison/03_databricks.md](../comparison/03_databricks.md) を埋める。
Tier A 3基盤との構造差（wheel / カタログの中のモデル / scale-to-zero / 到達経路）を
1節にまとめる。

## 9. ③' JSONL 回収スニペット（Volume → ローカル）

ジョブは Volume 上に `ml_runs.jsonl` を残す。`make collect` は
**ファイル名 `ml_runs.jsonl` を rglob で拾う**ので、落とすときに改名しない。

```python
from pathlib import Path
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
root = "/Volumes/<catalog>/<schema>/artifacts/runs"
local = Path("artifacts/fallback/databricks")
local.mkdir(parents=True, exist_ok=True)
for d in w.files.list_directory_contents(root):
    for e in w.files.list_directory_contents(f"{d.path}model"):
        if e.path.endswith("ml_runs.jsonl"):
            (local / "ml_runs.jsonl").open("ab").write(w.files.download(e.path).contents.read())
```

## 10. 有料プランで測り直す項目

Free Edition の制約に由来する行は、プランが変わると結果が変わる。

| 項目 | Free Edition の実測 | 有料で変わりうる点 |
|---|---|---|
| Terraform 網羅度 | **Catalog を作れない** | Default Storage でなければ作れる想定 |
| permission friction | **0件** | SP を分離できるので**本来の権限設計の試行回数**が出る |
| ③ Neon 到達 | collected | egress 制限の解除（LinkedIn 認証 / 有料）で direct を狙える |
| 同時実行 | 5タスクまで | 上限が上がる |
