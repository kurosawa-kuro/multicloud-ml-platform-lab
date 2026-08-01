# 動作検証: Vertex AI（Phase 1）

> ## ✅ 検証完了（2026-08-01）— **5基盤すべて完走済み**
>
> 完了条件8項目すべて実測で通過。⑧（比較レポート）も記述済み。
> **5基盤の中で唯一、apply も destroy も試行1回で通り、契約ゲートにも権限追加にも当たらなかった**
> （結論は [comparison/selection-checklist.md](../comparison/selection-checklist.md)）。
> 実測値と発見は [../comparison/01_vertex.md](../comparison/01_vertex.md) が正本。
>
> | 指標 | 実測 |
> |---|---|
> | apply / destroy | 17 resources 30.4s / 16 resources 20.2s |
> | 学習 | `JOB_STATE_SUCCEEDED`・attempt=2・ジョブ内 10.0s（投入→完了 259.3s） |
> | RMSE | **0.4368055090296257**（ローカル基準値と完全一致） |
> | Neon 到達 | **direct**（ジョブ内から直接 INSERT・全6行） |
> | register / deploy / predict | 154.7s / **1356.0s** / 2.0s |
> | 推論値 | `4.183217948107466`（クラウド = ローカル推論コンテナ = Booster 直予測） |
> | 残留 | 登録モデル1件（WARN・課金なし）→ 測定・記録後に掃除済み |
> | クラウド最終状態 | 本ラボ資産 **0件**（Endpoint/Model/Job/GCS/AR/tfstate すべて） |
>
> **次**: [動作検証-sagemaker.md](./動作検証-sagemaker.md)（Phase 2）。
> 本 runbook で確立した判定手順をそのまま写す。

| | |
|---|---|
| Tier | A（コンテナ実行型・統一単位 = 学習イメージ） |
| ENV / PLATFORM | `gcp-dev` / `vertex` |
| リージョン | us-central1（既存 GCP 資産と同一。他基盤と違う点はコスト比較の注記に残す） |
| 資格情報 | ADC（`gcloud auth application-default login`）。[credentials.md §2](./credentials.md) |
| 実装 | `src/platforms/vertex/adapter.py` / `docker/training/entrypoint_vertex.sh` |
| 位置づけ | **5基盤のアンカー**。ここで確立した判定手順を他4基盤へ写す |

共通の前提・8項目の定義・停止条件は [README.md](./README.md)。以下は Vertex 固有分のみ。

## 0. 他基盤へ写す教訓（Phase 2〜5 の着手前に読む）

Phase 1 で**実際に踏んだ**もの。同じ穴が他基盤にもある前提で先に潰す。

| # | 教訓 | 他基盤での確認 |
|---|---|---|
| 1 | `make deps` は基盤 SDK を入れない。**`make PLATFORM=<p> deps-platform` が必須** | 全基盤 |
| 2 | 配布物（イメージ / wheel / zip）の push・アップロードに make ターゲットが無い。手打ち | 全基盤 |
| 3 | `register` は `train` と同一プロセスを要求する。やり直しは **`resume --artifact-uri`** で再学習を避ける | 全基盤 |
| 4 | 「残留なし」は**検査側の欠陥でも出る**。まず引っかかる状態を作って検査の反応を確かめる | 全基盤 |
| 5 | SDK クライアントは **project / region を明示**して作る（環境変数任せだと別リージョンを見る） | 全基盤 |
| 6 | 撤退は **teardown（SDK）→ destroy（Terraform）** の順。逆だと消し残る | Tier A |
| 7 | 律速は学習ではなく**エンドポイント起動**（Vertex は 22.6 分）。待ち時間を工程に織り込む | Tier A |

## 1. 着手前チェック

全部緑になってから apply する。

```bash
gcloud auth application-default print-access-token >/dev/null && echo "ADC OK"
gcloud config get-value project
make PLATFORM=vertex deps-platform   # ★ Vertex SDK（gcp extra）を入れる
make test                            # 全 passed / 5 skipped（skip は Phase 0 プレースホルダのみ）
make train                           # RMSE がローカル基準値と一致するか
```

- [ ] ADC が通る / 対象 project が正しい
- [ ] **`make PLATFORM=vertex deps-platform` 済み**。`make deps` は基盤 SDK を入れない
      （学習コンテナを太らせないため基盤ごとに別 extra）。忘れると `phase-train` が
      `ModuleNotFoundError: No module named 'google'` = `failure_class=package` で
      1 attempt を消費する（2026-08-01 に実際に発生）
- [ ] `make train` の RMSE = `0.4368055090296257`
- [ ] Neon の `ml_runs` / `infra_events` / `cost_snapshots` が存在する（`make db-migrate` 済み）
- [ ] **git のワークツリーが clean**（少なくとも `src/core` / `docker/` / `pyproject.toml`）。
      `CODE_REVISION` はビルド時の `git rev-parse HEAD` を焼き込むので、未コミットの
      変更があると**記録した SHA が実際に動いたコードを指さない**（同一SHA担保が崩れる）

## 2. ① terraform apply

```bash
# 変数の export は不要。vertex_submitter_email は Doppler
# （MCML_TF_VERTEX_SUBMITTER_EMAIL = `gcloud config get-value account` の値）から入る。
make ENV=gcp-dev tf-init

# plan を保存して、**レビューしたものと同一の変更だけ**を適用する。
# tf-apply は -auto-approve を付けない（対話承認）ので、非対話環境では
# 保存済み plan を渡す（run_terraform.py は terraform 引数をそのまま通す）。
terraform -chdir=infra/environments/gcp-dev plan -out=/tmp/gcp-dev.tfplan
PYTHONPATH=src doppler run -- .venv/bin/python scripts/run_terraform.py \
  apply --env gcp-dev /tmp/gcp-dev.tfplan

terraform -chdir=infra/environments/gcp-dev output -json > artifacts/gcp-dev.outputs.json
```

`MCML_TF_VERTEX_SUBMITTER_EMAIL` が未設定だと apply 前に落ちる。以前は黙って `submitter_act_as` が作られず（17→16 リソース）、
ジョブ投入時に SA の actAs 権限不足で落ちる。

**outputs.json は必須。** `src/platforms/shared/config.py` は bucket / service account /
イメージ URI prefix をここから解決する。無いと adapter 構築時に
「terraform apply 後に output -json を実行するか…」の ConfigError で落ちる（exit 2）。

判定: `infra_events` に apply 1行（所要秒・リソース数つき）。

### apply の実行（5基盤共通）

**Terraform 変数は `export` しない。** `env/config.yaml` の `terraform:` 節（人が決める値）と
Doppler（秘密・個人識別子）から `run_terraform.py` が組み立てる（対応表は
`src/platforms/shared/terraform_vars.py`）。解決できない変数があれば **terraform を起動する前に**
名前を挙げて落ちる。

対話端末があるなら:

```bash
make ENV=gcp-dev tf-init && make ENV=gcp-dev tf-plan && make ENV=gcp-dev tf-apply   # yes を入力
```

**非対話（CI・バックグラウンド・エージェント）は保存済み plan を渡す。**
素の `tf-apply` は承認プロンプトが `EOF` で落ちるうえ、**`infra_events` に偽の
failure 行が残って「apply 試行回数」を汚す**（2026-08-01 に実際に発生）。
plan 無しで非対話実行するとガードが `EXIT_USAGE` で止める:

```bash
PYTHONPATH=src doppler run -- .venv/bin/python scripts/run_terraform.py \
  plan --env gcp-dev -out=/tmp/gcp-dev.tfplan
PYTHONPATH=src doppler run -- .venv/bin/python scripts/run_terraform.py \
  apply --env gcp-dev /tmp/gcp-dev.tfplan
```

⚠️ **plan も `run_terraform.py` 経由で回す。** 素の `terraform plan` は
config.yaml / Doppler 由来の `-var` を受け取らないため、**変数の抜けた plan が
保存され、apply がそれを適用してしまう**（2026-08-01 実測: 予算アラートが落ちて
`Plan: 9 to add`。正しくは 10）。plan は `infra_events` に記録しない。

destroy も同じ（`terraform destroy <plan>` は terraform 自身が拒否するので
`plan -destroy` の成果物を渡す。記録上の action は destroy のまま保たれる）:

```bash
PYTHONPATH=src doppler run -- .venv/bin/python scripts/run_terraform.py \
  plan --env gcp-dev -destroy -out=/tmp/gcp-dev-destroy.tfplan
PYTHONPATH=src doppler run -- .venv/bin/python scripts/run_terraform.py \
  destroy --env gcp-dev /tmp/gcp-dev-destroy.tfplan
```

## 3. 配布物の準備（自動化なし・手打ち）

`make docker-build` は**ローカルにビルドするだけ**で push しない。
Artifact Registry への push は make ターゲットが無いので手で打つ。

```bash
make docker-build && make docker-build-serving
PREFIX=$(jq -r '.container_image_prefix.value' artifacts/gcp-dev.outputs.json)
gcloud auth configure-docker "${PREFIX%%/*}"
docker tag mcml-training:latest "$PREFIX/training:latest" && docker push "$PREFIX/training:latest"
docker tag mcml-serving:latest  "$PREFIX/serving:latest"  && docker push "$PREFIX/serving:latest"
```

学習データも同様に手で置く（`GcsArtifactStore` は実装済みだが CLI から呼ぶ口が無い）。

```bash
BUCKET=$(jq -r '.gcs_bucket.value' artifacts/gcp-dev.outputs.json)
gcloud storage cp data/california_housing.parquet "gs://$BUCKET/data/california_housing/"
```

- [ ] イメージ2本が push 済み / タグは `common.image_tag`（既定 `latest`）と一致
- [ ] `CODE_REVISION` がビルド引数で焼き込まれている（`docker run --rm mcml-training:latest env | grep CODE_REVISION`）
- [ ] Parquet が `gs://<bucket>/data/california_housing/` にある

## 4. ②〜⑤ フェーズ実行

```bash
make PLATFORM=vertex phase-train      # ②
make collect                          # ③' JSONL fallback があれば回収
make PLATFORM=vertex phase-register   # ④
make PLATFORM=vertex phase-deploy     # ⑤a ⚠️ 常時課金
make PLATFORM=vertex phase-predict    # ⑤b
```

`register` は `train` と同一プロセスの成果物 URI を要求するので、単独実行では
「実行順の不足」で exit 2 になる。通しで回すなら `make PLATFORM=vertex phase-all`
（teardown は含まない）。

| # | 何を見て成功と判定するか |
|---|---|
| ② | CustomJob が `JOB_STATE_SUCCEEDED`。`ml_runs` に stage=train が1行（**失敗しても1行入る**のが正常。行が無い方が異常） |
| ③ | その行の `write_path='direct'`（ジョブ内から Neon 直 INSERT が届いた）。`collected` ならジョブから Neon へ届いていない＝到達経路の実測結果として記録する |
| ④ | Model Registry に `mcml-california-housing` が上がり、alias が付く |
| ⑤ | `phase-predict` が 200 を返し、`ml_runs` に stage=predict が1行 |

**③ が Vertex の主要観測点。** Tier A は「通常 egress で届く」が事前仮説なので、
`direct` にならなかった場合はそれ自体が Phase 1 の第一発見になる。

```sql
-- 到達経路の確認（sql/comparison_queries.sql の補助クエリ）
select platform, stage, write_path, count(*) from ml_runs where platform='vertex' group by 1,2,3;
```

## 4'. 推論アプリ（core/app）のローカル検証

Vertex のエンドポイントが叩くのは `/health` + `/predict` だけなので、**3契約すべては
クラウドでは検証されない**。推論イメージをローカルで上げて5ルートを通す。

```bash
docker run -d --name mcml-serving-test -p 18080:8080 \
  -v "$PWD/artifacts/local:/models:ro" -e MODEL_DIR=/models mcml-serving:latest
B=http://127.0.0.1:18080
P='{"instances":[{"med_inc":8.3252,"house_age":41.0,"ave_rooms":6.9841,"ave_bedrms":1.0238,"population":322.0,"ave_occup":2.5556,"latitude":37.88,"longitude":-122.23}]}'
curl -s $B/health; curl -s -d "$P" -H 'content-type: application/json' $B/predict
curl -s $B/ping;   curl -s -d "$P" -H 'content-type: application/json' $B/invocations
curl -s -d "$P" -H 'content-type: application/json' $B/score
docker rm -f mcml-serving-test
```

| 見るもの | 期待 | 2026-08-01 実測 |
|---|---|---|
| 5ルートの応答 | 全て 200 | ✅ |
| 3契約の予測値 | **完全一致**（境界で形だけ変換し predict() は1つ） | ✅ `4.183217948107466` で一致 |
| train/serve skew | ローカル Booster 直予測と一致 | ✅ 一致 |
| キー順を変えた入力 | 同じ値（学習時の列順に整列） | ✅ 一致 |
| 特徴量欠落 | 400 + 欠落した列名 | ✅ |
| `instances: []` | 422（契約違反） | ✅ |
| `x-request-id` | エコーされる / 構造化ログに載る | ✅ |

**クラウドの推論値ともここが一致していること**が最終確認
（2026-08-01: Vertex エンドポイントの応答も `4.183217948107466`）。

## 5. Vertex 固有の確認

- **Endpoint の器は Terraform 側にある。** SDK が作るのは Model のデプロイのみ。
  「IaC でどこまで書けたか」の比較軸なので、器まで SDK で作り直さない。
- **GCS FUSE**: 学習コンテナは `gs://<bucket>` を `/gcs/<bucket>` として見る
  （`adapter.GCS_FUSE_ROOT`）。パスがずれていれば data 読み込みで落ちる。
- **成果物の出力先は `AIP_MODEL_DIR`**（`base_output_dir` 経由）。`--output-uri` ではない。
- **Spot（`use_spot: true` が既定）**。中断は失敗 run として記録される。
  中断を避けるために spot を切らない（切るならその判断を comparison ページに書く）。

## 6. 失敗時の切り分け

`ml_runs.failure_class` で分岐する。**権限エラーを広い権限で潰さない**。

| failure_class | 典型 | 対処 |
|---|---|---|
| `permission` | service account に AI Platform / GCS 権限が足りない | ロールを**1つずつ**足して再実行。attempt が増えるのが正しい |
| `quota` | リージョンの GPU/CPU quota | マシンサイズを落とすか quota 申請。事実を記録 |
| `network` | ジョブから Neon へ届かない | 到達経路の実測結果。JSONL fallback を `make collect` で回収 |
| `container` | イメージの起動失敗 | シムのパス（`/app/entrypoint_vertex.sh`）とタグを確認 |
| `data` | 入力 Parquet が無い / 列が違う | GCS の配置と `FEATURE_COLUMNS` を確認 |

## 7. ⑥⑦ teardown と残留検査

```bash
make PLATFORM=vertex phase-teardown   # Endpoint を落とす（destroy より先）
make ENV=gcp-dev tf-destroy           # destroy → check-residual まで連鎖
```

**順番を守る。** Endpoint が残ったまま destroy すると HTTP 400 で落ちる。

`scripts/check_residual.py` が Vertex で見るもの（2026-08-01 実測で更新）:

| kind | severity | 実測 |
|---|---|---|
| `vertex_endpoint` | FAIL | 0件（1件でもあれば課金が続いている） |
| `gcs_object` | WARN | 0件。**Terraform 管理下 + `force_destroy` で成果物ごと消える** |
| `artifact_registry` | WARN | 0件。同上 |
| `registered_model` | WARN | **1件残る**。SDK が作るので destroy でも teardown でも消えない |

FAIL / ERROR が1件でもあれば exit 1。**ERROR は「残留ゼロ」ではなく「検査できなかった」**
なので、緑と読み替えない。

> ⚠️ **「残留なし」を鵜呑みにしない。** 2026-08-01 の実測では、検査側の欠陥
> （`registered_model` 項目の欠落 / `aiplatform.init()` 未呼び出しによる**別リージョン参照** /
> 共有プロジェクトの全バケット列挙）で「残留ゼロ」が3回続けて出た。いずれも嘘だった。
> 新しい基盤の検査を書いたら、**まず何かが引っかかる状態を作って**検査が反応することを
> 確かめてから、ゼロを信用する。

### 掃除（測定 → 記録 → 掃除）

残留は測定・記録してから消す。記録は `infra_events` に入るので、オブジェクトを
残す理由は無い（次フェーズの残留測定に前回の残骸が混ざる方が害）。

```bash
gcloud ai models delete <MODEL_ID> --region=us-central1
# CustomJob 履歴は gcloud に delete が無い（SDK のみ・非課金なので任意）
```

## 8. ⑧ レポート記述

[docs/comparison/01_vertex.md](../comparison/01_vertex.md) の完了条件8項目表と実測値表を埋める。
数値は `sql/comparison_queries.sql` の結果から起こす（手で数えない）。
**このページを書き終えるまで Phase 2 に進まない**（`01_requirements.md` のブロック条件）。

構造仮説（着手前の予想）が外れた場合、予想を書き換えず「予想 → 実測 → 差分」で残す。

→ **2026-08-01 に記述完了。ブロック条件は解除**。

## 9. 深掘りせず送った論点（owner 判断 2026-08-01）

**5基盤を先に完走させる**のが優先。ここで立ち止まると終わらないため、
次の論点は Phase 1 では**深掘りしない**と決めた。記録だけ残す。

| 論点 | 現状の扱い | 再開の条件 |
|---|---|---|
| permission friction が train 以外ゼロ | Terraform で先に正解ロールを与えたため測れていない。事実として comparison に明記済み | **SP へ移行できたら使う位置づけ**（owner 原文） |
| `CustomJob` 実行履歴を残留検査に入れるか | 入れない。課金されない実行ログでリソース残留とは性質が違う。destroy 後も残る事実だけ記録 | 同上 |
| 残留の分類粒度（Tier A / Tier B の土俵合わせ） | Phase 5 まで実測を集めてから設計 | 5基盤完走後 |

5基盤ぶんの実測が揃ってから、共通する差分だけを掘る。
