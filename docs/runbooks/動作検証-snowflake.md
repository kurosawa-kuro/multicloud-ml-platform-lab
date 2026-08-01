# 動作検証: Snowflake（Phase 5）

> ## ✅ 完走（2026-08-01）— 8項目すべて達成
>
> トライアル（Standard・AWS_AP_NORTHEAST_1・`ABCDEFG-HI12345`・サーバー `10.26.102`）で
> **①〜⑧すべて実測で通過**。実測値と一次記録は
> [../comparison/05_snowflake.md](../comparison/05_snowflake.md) が正本。
>
> | 指標 | 実測 |
> |---|---|
> | RMSE | **0.4368055090296257**（ローカル基準値と16桁一致） |
> | 1件推論 | **4.183217948107466**（ローカル / Vertex / Snowflake の4経路で一致） |
> | run friction | train 2 / register **5** / deploy 1 / predict 1 |
> | infra friction | apply **4回**（うち失敗3） |
> | stage 別所要 | train 53.4s / register 78.7s / deploy 0.5s / predict 22.3s |
> | Neon 到達 | **collected**（トライアルは direct 不可） |
> | 残留 | FAIL 0件（stage blob + Fail-safe 固定行） |
>
> ### 無料トライアルで踏む地雷は3つ（すべて対処済み・下記に手順化）
>
> 1. **External Access Integration が作れない** → Neon 直到達は不可（§2）
> 2. **モデル登録が原因不明の内部エラーで落ちる** → `conda_dependencies` 必須（§4）
> 3. **`SNOWFLAKE_ACCOUNT` が provider と衝突する** → terraform 実行時だけ外す（§2）

| | |
|---|---|
| Tier | B（データ基盤内蔵型・統一単位 = **stage へ置く zip**） |
| ENV / PLATFORM | `sf-dev` / `snowflake` |
| 資格情報 | キーペア認証。`SNOWFLAKE_*` 8件（[credentials.md §5](./credentials.md)）。**Terraform は `ACCOUNTADMIN` / adapter は `MCML_DEV_ROLE`** |
| 実装 | `src/platforms/snowflake/adapter.py` / `src/platforms/snowflake/sproc_handler.py` |
| 着手前 | snowflake-phase-precheck.md を消化してから |
| タイムボックス | **トライアル期限が実質の期限**。分散実行せず一気に完走する |

共通の前提・8項目の定義・停止条件は [README.md](./README.md)。以下は Snowflake 固有分のみ。

**この基盤だけ「deploy」に相当するリソースが無い。** 手順表の⑤の形が他4基盤と違う。

## 1. 着手前チェック

```bash
make PLATFORM=snowflake deps-platform   # Snowpark + snowflake-ml-python
make test
doppler secrets | grep SNOWFLAKE        # 8件そろっているか（値は見ない）
```

- [ ] `make PLATFORM=snowflake deps-platform` 済み（`make deps` は基盤 SDK を入れない）
- [ ] `SNOWFLAKE_*` 8件が Doppler にある（[credentials.md §5](./credentials.md) の表）
- [ ] 秘密鍵が Doppler にあり、**ローカルにも Snowflake の workspace にも残っていない**
      （リポジトリ直下に置かない。`.gitignore` の `*.p8` は保険であって置き場ではない）
- [ ] **公開鍵が Snowflake のユーザーに登録済み**で、手元の秘密鍵と対応している:
      `openssl rsa -in <key>.p8 -pubout | grep -v "^-----" | tr -d '\n'` が
      `DESC USER <user>` の `RSA_PUBLIC_KEY` と一致する
- [ ] precheck 項目5に結論（**実測済み: 範囲指定子は受理される / lightgbm 4.6.0・
      scikit-learn 1.8.0 は channel にある / `snowflake-ml-python` は 1.48 が上限**）
- [ ] トライアルの残クレジットと期限を確認し、完走できる見込みがある
- [ ] Phase 4 の go/no-go 結果と comparison ページの状態を確認した

接続確認（**ここが通らないと以降すべて無駄撃ち**）:

```bash
PYTHONPATH=src doppler run -- .venv/bin/python -c "
import dataclasses
from snowflake.snowpark import Session
from platforms.config import load_settings
from platforms.snowflake.adapter import connection_parameters
cfg = dataclasses.replace(load_settings().snowflake(), role='', database='SNOWFLAKE',
                          schema='INFORMATION_SCHEMA', warehouse='COMPUTE_WH')
s = Session.builder.configs(connection_parameters(cfg)).create()
print(s.sql('select current_account_name(), current_user(), current_version()').collect())
"
```

### 罠: データ層を借りない

`Snowflake-Labs/sfguide-snowpark-scikit-learn` の California Housing は **Kaggle/handson-ml 版**
（`OCEAN_PROXIMITY` 等・One-Hot あり）で、本ラボの `fetch_california_housing` とは
列も目的変数スケールも別物。**配管だけ流用し、データ層は必ず差し替える。**
見落とすと Snowflake だけ RMSE が合わず、原因究明で期限を溶かす。

## 2. ① terraform apply

### 地雷1: `SNOWFLAKE_ACCOUNT` を環境から外す

provider v2 は `SNOWFLAKE_ORGANIZATION_NAME` + `SNOWFLAKE_ACCOUNT_NAME` を使う。
connector 用の `SNOWFLAKE_ACCOUNT`（`<org>-<account>` 形式）が同時に環境にあると
provider がそちらを拾い、**`PROVIDER_CONFIGURATION_ACCOUNT_FALLBACK experiment` を要求して落ちる**。
`doppler run` は両方を注入するので、**terraform 実行時だけ外す**。

### 地雷2: トライアルは External Access Integration を作れない

```
509009 (0A000): External access is not supported for trial accounts.
```

**Neon 直到達の手段が存在しない**（「重い」ではなく「不可」）。
`neon_host=""` / `create_neon_secret=false` で network rule / secret / EAI を作らない構成にする。
③ が `collected` になるのはこれが理由。有料アカウントなら3点セットを有効にして測り直す。

```bash
# 変数の export は不要。create_neon_secret は env/config.yaml の
# terraform.sf-dev 節、grant_to_user は Doppler（MCML_TF_SF_GRANT_TO_USER）。
PLAN=/tmp/sf-dev.tfplan

doppler run --preserve-env -- env -u SNOWFLAKE_ACCOUNT \
  terraform -chdir=infra/environments/sf-dev init
doppler run --preserve-env -- env -u SNOWFLAKE_ACCOUNT \
  terraform -chdir=infra/environments/sf-dev plan -out="$PLAN"

# 保存済み plan を適用（レビューした内容と一致させる。承認プロンプトも出ない）
PYTHONPATH=src doppler run --preserve-env -- env -u SNOWFLAKE_ACCOUNT \
  .venv/bin/python scripts/run_terraform.py apply --env sf-dev "$PLAN"

doppler run --preserve-env -- env -u SNOWFLAKE_ACCOUNT \
  terraform -chdir=infra/environments/sf-dev output -json > artifacts/sf-dev.outputs.json
```

grant が `object does not exist or not authorized` で落ちることがある（一過性）。
**再実行で解消する。** 実測 4 回中 1 回発生。

Terraform が持つもの: Database / Schema / Warehouse / Role / Grants / Stage
（+ 有料なら Network Rule / Secret / EAI）。
`config.py` が読む outputs: `database_name` / `schema_name` / `warehouse_name` /
`stage_name` / `role_name`。

### apply 直後に必ず確認する: adapter が名乗るロール

```bash
PYTHONPATH=src doppler run -- .venv/bin/python -c "
from snowflake.snowpark import Session
from platforms.config import load_settings
from platforms.snowflake.adapter import connection_parameters
s = Session.builder.configs(connection_parameters(load_settings().snowflake())).create()
print(s.sql('select current_account_name(), current_role(), current_warehouse()').collect())
"
```

**`current_role()` が `MCML_DEV_ROLE` であること。** `ACCOUNTADMIN` が返ったら
outputs の `role_name` を拾えていない（`artifacts/sf-dev.outputs.json` の再生成を忘れている）。
そのまま進めると権限エラーが一度も起きず、**Snowflake だけ permission friction が
ゼロになって比較表に穴が空く**。ここで止めて直す。

> `CURRENT_ACCOUNT()` は**アカウント locator**（実測 `RQ56090`）を返し、接続に使う
> アカウント名（`HI12345`）とは別物。照合には `CURRENT_ACCOUNT_NAME()` を使う。

プロバイダ側の注意（precheck 項目4）: source 名リネームへの `versions.tf` 追随と、
**プレビュー機能の既定無効**（`preview_features_enabled` に機能名を明示追加）。
External Access Integration がプレビュー扱いかを `MIGRATION_GUIDE.md` で確認する。

### tfstate の初期化（Tier B は Neon backend）

**Tier B は自前で tfstate を置けない。** UC Volume も Snowflake stage も Terraform
backend ではなく、オブジェクトストレージが外に無い。以前は GCP のバケットへ
相乗りしていたが、**GCP を畳むと Tier B 2基盤の state を失う**ため、
比較対象に含まれない Neon（計測 DB）を中立の置き場にした。

```bash
doppler run --command 'terraform -chdir=infra/environments/sf-dev init \
  $(.venv/bin/python scripts/tf_backend.py sf-dev)'
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
make ENV=sf-dev tf-init && make ENV=sf-dev tf-plan && make ENV=sf-dev tf-apply   # yes を入力
```

**非対話（CI・バックグラウンド・エージェント）は保存済み plan を渡す。**
素の `tf-apply` は承認プロンプトが `EOF` で落ちるうえ、**`infra_events` に偽の
failure 行が残って「apply 試行回数」を汚す**（2026-08-01 に実際に発生）。
plan 無しで非対話実行するとガードが `EXIT_USAGE` で止める:

```bash
PYTHONPATH=src doppler run -- .venv/bin/python scripts/run_terraform.py \
  plan --env sf-dev -out=/tmp/sf-dev.tfplan
PYTHONPATH=src doppler run -- .venv/bin/python scripts/run_terraform.py \
  apply --env sf-dev /tmp/sf-dev.tfplan
```

⚠️ **plan も `run_terraform.py` 経由で回す。** 素の `terraform plan` は
config.yaml / Doppler 由来の `-var` を受け取らないため、**変数の抜けた plan が
保存され、apply がそれを適用してしまう**（2026-08-01 実測: 予算アラートが落ちて
`Plan: 9 to add`。正しくは 10）。plan は `infra_events` に記録しない。

destroy も同じ（`terraform destroy <plan>` は terraform 自身が拒否するので
`plan -destroy` の成果物を渡す。記録上の action は destroy のまま保たれる）:

```bash
PYTHONPATH=src doppler run -- .venv/bin/python scripts/run_terraform.py \
  plan --env sf-dev -destroy -out=/tmp/sf-dev-destroy.tfplan
PYTHONPATH=src doppler run -- .venv/bin/python scripts/run_terraform.py \
  destroy --env sf-dev /tmp/sf-dev-destroy.tfplan
```

## 3. 配布物の準備（zip）

```bash
make sf-package     # stamp-revision に依存。artifacts/dist/core_ml.zip
```

zip の中身は `src/core` + `src/platforms/{__init__.py,neon,snowflake}` のみ
（他基盤の adapter は入らない。実測 52 ファイル）。
stage への PUT は `adapter.upload_to_stage()` が `@<db>.<schema>.CODE/dist/` へ置く。

- [ ] `make stamp-revision` 済み（zip に .git も `CODE_REVISION` も無い）
- [ ] PUT は `auto_compress=False`（再圧縮されると sproc の `IMPORTS` が読めない）

## 4. ②〜⑤ フェーズ実行

```bash
make PLATFORM=snowflake phase-train
make collect COLLECT_DIR=<stage から落とした JSONL の置き場>    # ③' 必ず要る
make PLATFORM=snowflake phase-register
make PLATFORM=snowflake phase-deploy
make PLATFORM=snowflake phase-predict
```

| # | 何を見て成功と判定するか |
|---|---|
| ② | `CREATE OR REPLACE PROCEDURE` → `CALL` が成功し、summary の metrics が返る。**実行資源は DDL + CALL のみ**（ジョブ資源が存在しない）。実測 53.4s |
| ③ | **必ず `collected`**。warehouse に psycopg が無く、さらにトライアルは EAI 不可。sproc は JSONL を stage へ PUT する。到達不能そのものが比較データ |
| ④ | Model Registry に版ができる。**stage の model.txt を Booster に復元してから `log_model`**（Tier A のように URI を渡すだけでは登録できない）。実測 78.7s |
| ⑤ | warehouse 推論が値を返す。**HTTP ではなく SQL / Python API**。`docker/serving` の3契約はこの基盤では使われない。実測 22.3s / `4.183217948107466` |

### 地雷3: 登録は `conda_dependencies` が無いと必ず落ちる（原因が一切出ない）

**この基盤で最も時間を溶かす箇所。** 既定のままだと `log_model` が下記で落ちる。

```
603 (XX000): SQL execution internal error:
Processing aborted due to error 300002:...; incident <id>
```

**クライアント側は packaging も upload も完全に成功する**ので、エラーからは何も分からない。
原因は依存の解決経路:

```
アカウント capability: ENABLE_PIP_ONLY_PACKAGING = true
  ↓ SDK が MANIFEST.yml に pip 経路を書く
  artifact_repository_map: {pip: SNOWFLAKE.SNOWPARK.PYPI_SHARED_REPOSITORY}
  ↓ CREATE MODEL 時にサーバーが PyPI を取りに行く
  ↓ トライアルは external access 不可（地雷2と同じ制限）
内部エラー
```

対処は `log_model(..., conda_dependencies=[...])`（`adapter.MODEL_CONDA_DEPENDENCIES`）。
Anaconda channel 経路になり外部アクセスが要らなくなる。**実装済みなので通常は意識不要**だが、
版を変えるときは channel の在庫を必ず確認する:

```sql
select version from information_schema.packages
where package_name in ('scikit-learn','lightgbm','snowflake-ml-python') and language='python';
```

`snowflake-ml-python` は **channel 在庫の上限が pyproject の pin を決める**
（実測 2026-08-01: ローカル 1.49.0 に対し channel 最大 1.48.0）。

同じ症状が出たら、まず **`MANIFEST.yml` を stage から取得して中身を読む**。
エラーメッセージからは分からないが manifest には経路が書いてある。

```bash
# stage に上がった manifest を読む（原因特定はこれが最短）
snow sql -q "get @<stage>/model/MANIFEST.yml file:///tmp/"
```

### deploy（⑤a）の扱い

`phase-deploy` は**既定バージョンを切り替えるだけ**で、インフラを何も作らない。
`ml_runs.params` に `no_endpoint_resource=true` が入るので、比較表ではこの非対称性を
「デプロイ = リソース無し」として残す（0秒だから速い、と読み替えない）。

SPCS でサービス化する道はあるが**主経路にしない**（要件で決定済み。差分メモ止まり）。

## 5. Snowflake 固有の確認

- **PACKAGES のバージョン固定**（`SPROC_PACKAGES`）。無指定だと channel の最新が入り、
  同一SHAでも RMSE がずれる。範囲指定子（`>=4.6,<4.7`）は**受理される**（実測）。
  正本は `pyproject.toml`（`tests/test_snowflake_adapter.py` が一致を pin）。
- **`SPROC_PACKAGES` には pyarrow が要る。** `session.table().to_pandas()` は connector の
  pandas 経路を通り pyarrow を要求する。入れないと warehouse 内で
  `255002: Optional dependency: 'pandas' is not installed` になる
  —— **メッセージは pandas と言うが、足りないのは pyarrow**（実測で1 attempt 消費）。
- **sproc の PACKAGES と registry の conda_dependencies は別物。** 前者は学習の実行環境、
  後者は登録するモデルの実行環境。片方だけ直しても通らない。
- **モデルがスキーマ内オブジェクト**（`database.schema.model`）。独立レジストリではない。
- 事前に `CALIFORNIA_HOUSING` テーブル（sklearn 版）を用意しておく。
  `session.write_pandas(df, 'CALIFORNIA_HOUSING', auto_create_table=True, overwrite=True,
  quote_identifiers=False)` で投入できる（列は大文字に畳まれるが `normalize_columns` が吸収する）。

## 6. 失敗時の切り分け

| failure_class | 典型 | 対処 |
|---|---|---|
| `permission` | role に stage / warehouse の grant 不足 | 1つずつ付与 |
| `network` | EAI 経由でも Neon へ届かない | 想定内。JSONL fallback を確認 |
| `sdk` | `CREATE MODEL` の内部エラー（603 / 300002） | **§4 の地雷3**。`conda_dependencies` を確認 |
| `data` | Kaggle 版データの混入 | 列名と RMSE の桁を確認。**最優先で疑う** |

> ⚠️ **`failure_class` を鵜呑みにしない。** 実測で、pyarrow 不足（実体は `package`）が
> `network` と誤分類された。分類器がエラー全文のヒント語に当たった結果で、
> 未分類にはならない設計だが**内訳を読むときは `error_excerpt` と突き合わせる**。

### 603 / 300002 の内部エラーに当たったときの切り分け順

Snowflake は 300xxx 系を「ユーザー側で対処不能・query_id を添えてサポートへ」と定義しているが、
**クライアント側の設定で回避できる場合がある**（今回がそれ）。次の順で潰すと速い。

1. `MANIFEST.yml` を stage から取得して**依存の解決経路**を見る（pip か conda か）← 最短
2. `information_schema.packages` で**版が channel にあるか**（`in` 判定。文字列ソートは誤る）
3. 最小モデル（`LinearRegression`）で再現するか → するならモデル種別は無関係
4. `select last_query_id()` で **query_id を確保**（サポート照会に必要。incident ID だけでは不足）

## 7. ⑥⑦ teardown と残留検査

```bash
# 変数は config.yaml / Doppler から入る

PYTHONPATH=src doppler run -- .venv/bin/python scripts/run_phase.py snowflake teardown
PYTHONPATH=src doppler run --preserve-env -- env -u SNOWFLAKE_ACCOUNT \
  .venv/bin/python scripts/run_terraform.py destroy --env sf-dev -auto-approve
PYTHONPATH=src doppler run -- .venv/bin/python scripts/check_residual.py --platform snowflake --record
```

| kind | severity | 実測（2026-08-01） |
|---|---|---|
| `warehouse_running` | FAIL | **0件**（destroy 済み） |
| `schema_object` | WARN | 0件（DB ごと destroy されるためモデル版も消える） |
| `stage_file` | WARN | 1件。**ただし本ラボの stage ではない**（下記の未対応） |
| `fail_safe` | WARN | **必ず1件出る固定行**（7日・設定で消せない） |

> **⚠️ 残留検査は「撤退で消える権限」に依存してはいけない。**
> `check_residual` が adapter と同じ `MCML_DEV_ROLE` で接続していたため、destroy 後は
> `Role 'MCML_DEV_ROLE' does not exist` で**検査自体が成立しなかった**（実測）。
> 現在はロールを名乗らない接続に修正済み。**ロールもカタログの中にあり destroy の対象になる**
> という Tier B 固有の落とし穴で、Tier A には無い。
>
> **未対応**: Vertex で入れた「ラボ資産だけを数える」絞り込み（`LAB_NAME_PREFIX`）が
> Snowflake 側に未適用。`stage_file: BLOBS` は本ラボのものではない。次フェーズ前に入れる。

`fail_safe` は消し忘れではない。**「残留ゼロ」と誤読させないための固定行**であり、
Tier A の残留と同じ土俵で数えない。Time Travel も同様に Tier B 特有の残留として扱う。

## 8. ⑧ レポート記述

→ **2026-08-01 に記述完了**。[docs/comparison/05_snowflake.md](../comparison/05_snowflake.md)。

「デプロイに相当するリソースが無い」は**リソースを作らない経路が存在する**と書く
（SPCS を選べば専用資源が立つ。今回は未測定）。Fail-safe が消せないことと併せて
選定チェックリスト（[selection-checklist.md](../comparison/selection-checklist.md)）へ反映する。

friction は [00_method.md](../comparison/00_method.md) の集計境界に従い
**run（`ml_runs`）と infra（`infra_events`）を別掲**する。合算しない。

## 9. 有料アカウントで測り直すべき項目

トライアル固有の制限で**測れなかった／条件が違った**もの。有料化したらここだけ再実行する。

| 項目 | トライアルでの結果 | 有料で変わりうる点 |
|---|---|---|
| ③ Neon 到達 | `collected` 固定 | **EAI が作れる**ので `direct` になりうる。Tier B の到達コストを初めて測れる |
| ④ 登録 | `conda_dependencies` 必須 | pip 経路（PyPI 参照）が通るなら既定のままで登録できる |
| ⑤ 推論 | warehouse 推論のみ | SPCS のモデルサービングが選べる。**デプロイのリソース有無**を測り直す |
| 残留 | stage blob + Fail-safe | EAI / secret / network rule の残留が新たに加わる |

**ただし比較表は「トライアルで測った」ことを明記したまま残す。**
契約段階が機能を切るという事実自体が、Azure の offer 制限と並ぶ比較材料になる。
