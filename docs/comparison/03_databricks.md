# Databricks

> 実測日 2026-08-01 / **Free Edition**（serverless 専用 / AWS `us-east-2` メタストア）
> workspace `dbc-xxxxxxxx-xxxx` / databricks-sdk 0.123.0 / provider 1.123.0 /
> serverless environment version 3（Python 3.12.3）
> 実行手順と合否判定: [../runbooks/動作検証-databricks.md](../runbooks/動作検証-databricks.md)

Tier: B（データ基盤内蔵型・統一単位 = **wheel**）

## 構造仮説（着手前の予想・実測で検証する）

- **Terraform 網羅度が最大**（ガバナンスオブジェクトまで到達する）
- scale-to-zero + job cluster 自動終了でアイドル課金リスクが最も低い
- 統一単位は wheel。ML Runtime プリインストール版との依存衝突が最大の懸念

| 予想 | 実測 | 差分 |
|---|---|---|
| Terraform 網羅度が最大 | **概ね当たり。ただし Free Edition では Catalog を作れない**（Default Storage） | 網羅度の上限が**プラン**で決まった。コード側の限界ではない |
| 依存衝突が最大の懸念 | **外れ。衝突は起きなかった** | 実際に効いたのは**依存の版ではなく Python 本体の版**（環境 v2=3.11 で wheel が入らない） |
| scale-to-zero でアイドル課金が最小 | 当たり（構造として） | ただし**エンドポイント作成に十数分**かかり、Tier A と時間の性質が違う |
| —（予想していなかった） | **成功・失敗の判定が壊れていた**（戻り値が無視され、失敗が SUCCESS に見える） | 本フェーズ最大の発見。下記「詰まった点」 |

## 完了条件8項目

| # | 項目 | 結果 |
|---|---|---|
| ① | terraform apply | ✅ **2回目で成功**（7リソース / 5.9s。1回目は Catalog 作成で失敗） |
| ② | 学習ジョブ成功（失敗試行も記録済み） | ✅ **attempt 3 で成功**（ジョブ内学習 8.5s / 投入〜完了 55.1s） |
| ③ | Neon へメトリクス到達 | ✅ **collected**（psycopg 不在。予想どおり） |
| ④ | モデル登録 | ✅ **attempt 2 で成功 / 55.2s**（MLflow をジョブ内で起動）。⑤の依存修正後に再登録 = attempt 3 / 66.1s |
| ⑤ | 1件オンライン推論 | ✅ **attempt 3 でデプロイ成功 / 475.7s** → 推論 3.0s / 予測値 `4.183217948107466` |
| ⑥ | terraform destroy | ✅ **2回目で成功**（1回目はモデル版が残っていて拒否） |
| ⑦ | 残留リソース記録 | ✅ **残留ゼロ**（FAIL/WARN とも 0 件。独立確認済み） |
| ⑧ | 比較レポート（本ページ） | ✅ |

## 実測値

| 指標 | 値 | 出典 |
|---|---|---|
| RMSE | **0.4368055090296257** | metric parity |
| 予測値 | **4.183217948107466** | 同上 |
| code_revision | `1bf0ed95` の run（wheel に stamp 済み） | 同上 |
| **run friction**（実行時） | train **3** / register **3** / deploy **3** / predict **1** | permission friction |
| failure_class の内訳（run のみ） | `sdk` × 6（**permission は 0 件**） | 同上 |
| **infra friction**（構築時） | apply **3回**（失敗1 / 環境更新1）・destroy **2回**（失敗1） | infra_events |
| stage 別所要 | train 55.1s（ジョブ内 8.5s）/ register 66.1s / **deploy 475.7s** / predict 3.0s / teardown 3.2s | stage 別所要 |
| Neon 到達経路 | **collected 1 / direct 0** | 到達経路内訳 |
| Terraform でカバー | Schema / Volume / Grants ×3 / Registered Model / **Job 定義** / Cluster Policy | 手記述 |
| SDK に残った範囲 | ジョブ起動・**モデル版の作成**・Serving Endpoint | 手記述 |
| destroy 後の残留 | **ゼロ**（ただし teardown でモデル版を消すのが前提） | teardown 品質 |
| アイドル時課金 | serverless（起動時のみ）+ serving の scale-to-zero | 手記述 |

集計境界は [00_method.md](./00_method.md)「friction の集計境界」に従い **run と infra を別掲**。

### permission friction が 0 件だった意味

**6回の失敗はすべて権限以外**（Python 版・戻り値契約・Volume の I/O・MLflow の実験・SDK の必須引数）。
Vertex の IAM 直しと違い、Databricks は `TF_VAR_job_principal` に付けた
catalog/schema/volume/model の grants が**一発で通った**。
ただしこれは **単一ユーザーのワークスペースでオーナー自身が実行**しているためで、
Free Edition ではアカウントコンソールが無くサービスプリンシパルを作れない。
**この 0 は「権限設計が楽」ではなく「権限を分離できていない」の 0** であり、
Tier A の試行回数と同じ土俵で比べない。

## 詰まった点（一次記録）

### 判定が壊れていた —— 失敗が SUCCESS として記録された

**`python_wheel_task` は entry point を関数として呼ぶ。** `main()` が `return 1` しても
握り潰され、**task は SUCCESS**。実際に学習が例外で落ちた run が
「② 成功」として adapter に記録された（緑の嘘）。ジョブ自身が書いた JSONL に
`status=failure` が残っていたことで気付いた。

```
adapter が見た結果 : Job run result_state = SUCCESS
ジョブが書いた行   : status=failure / "core.ml.cli が exit 1"
```

**所有者を2つに分けた設計（成功行はジョブ、投入失敗は adapter）がそのまま検出器になった。**
片方だけを信じていたら、この Phase は「1発で成功」と記録されていた。

修正は console script を `cli()` に分け `SystemExit` で返すこと。ただし
**`SystemExit(0)` は逆に失敗扱いになる**（`Workload failed` / `error: SystemExit: 0`）ので、
成功時は何も送出しない。この非対称は他の Tier B（Snowflake sproc）には無い。

### UC Volume は「普通のパス」ではない

`OSError: [Errno 29] Illegal seek`。**FUSE マウントで seek できない**ため、
`pandas.to_csv` と JSONL の append が落ちる。`model.txt`（LightGBM が自前で書く）
だけが残り、**成果物が半端に揃った状態**になるので「保存は成功した」ように見える。

対処は学習も記録も**ローカルに書いてから Volume へコピー**。
Tier A のオブジェクトストレージ（GCS/S3/Blob）は SDK 経由の upload なのでこの問題が無い。
**Volume は「ファイルシステムに見えるが違う」**という Tier B 固有の落とし穴。

### ④ 登録は SDK では不可能 —— MLflow クライアントが唯一の経路

`w.model_versions` に `create` が無い（`get/list/update/delete` のみ）。公式も
「Creating new model versions requires use of the MLflow Python client」と明記。
さらに **UC の版は model signature 必須**。

結果、**登録のためにジョブをもう1本起こす**構成になった（`--stage register`）。
④ の所要（55.2s / 66.1s）の大半はジョブ起動で、**Tier A の「API 1本」とは質が違う**。

これは Snowflake の「登録が復元済みモデル + 入力サンプル + 依存解決を要求した」と
同じ性質で、**Tier B は登録がサーバー側のモデル理解を伴う**という共通の構造差。
Databricks 固有の追加条件は **wheel task に既定の MLflow 実験が無い**こと
（`No experiment was found`。notebook 前提の API を job から使うと露出する）。

### Free Edition が Terraform 網羅度の上限を決めた

`cannot create catalog: Metastore storage root URL does not exist.`
**SDK で直接叩いても同じ**なので provider のバグではない。Default Storage の
メタストアには storage root が無く、API 経由の `CREATE CATALOG` は
MANAGED LOCATION を要求する（UI からなら作れる）。

既存カタログ `workspace` に相乗りし、**スキーマ名にラボ接頭辞**を入れて回避した。
**「Terraform でどこまで書けたか」がプランで変わる**という、他基盤には出なかった軸。

### PyPI へは届いた（Snowflake との決定的な差）

Free Edition は「outbound internet access is restricted to a limited set of trusted domains」
だが、**wheel の依存（lightgbm / scikit-learn / pandas / pyarrow）は PyPI から入った**。
Snowflake トライアルで④を止めた「外部リポジトリ不可」はここでは起きない。
egress 制限が効くのは **Neon のような任意の外部ホスト**の側。

### ⑤ デプロイは十数分かかり、1回失敗している

| 試行 | 結果 |
|---|---|
| 1 | 依存衝突で **DEPLOYMENT_FAILED**（下記） |
| 2 | 作成中に再実行して `ResourceConflict`（**IN_PROGRESS 中は触れない**） |
| 3 | ✅ 475.7s で READY |

**serving は毎回コンテナをビルドする。** Vertex の「エンドポイント作成 → モデル配置」と
所要の桁が違い、`scale-to-zero` の裏返しとして**初回の立ち上げが重い**。

#### 依存衝突は Snowflake の④と同型だった

```
The user requested pyarrow==21.0.0          ← MLflow が実行環境から自動推論
mlflow 2.21.3 depends on pyarrow<20         ← serving イメージ側の要求
→ CondaEnvException: Pip failed → コンテナ作成失敗
```

**クライアント側は最後まで成功して見える**（登録も作成要求も通る）。
十数分ビルドしてから失敗し、しかも `build_logs` API は
**pending config を読まない**（`config version 0` を見に行き `ResourceDoesNotExist`）。
REST を直接叩いて初めて原因が出た。

Snowflake は `conda_dependencies` を明示して解決した。Databricks は
`pip_requirements` を明示して解決した。**Tier B は「サーバー側で環境を再構築する」ため、
クライアントが成功しても後段で落ちる**という同じ構造を持つ。

## 撤退時に残ったもの

```
-- FAIL/ERROR 0 件 / 全 0 件
```

**残留ゼロ。** Serving Endpoint / Job / Cluster Policy / Schema / Volume / Registered Model
のいずれも残らなかった（SDK で独立に再確認済み）。Snowflake の `fail_safe`（消せない）や
Vertex の登録モデル残りと違い、**Tier B でありながら痕跡が残らない**。

ただし**無条件ではない**。

### `destroy` を止めたのは SDK が作ったモデル版

```
Error: cannot delete registered model:
  Function 'workspace.mcml_dev.california_housing' is not empty. The function has 2 model versions(s)
```

`databricks_registered_model` に **force_destroy が無い**（provider schema で確認）。
`databricks_catalog` / `schema` / `volume` にはあるので、**器ごとに撤退の強さが違う**。

**Vertex は登録モデルが残っても destroy が通る**（残留として記録されるだけ）。
Databricks は **destroy そのものが止まる**。撤退の失敗モードが違うので、
`teardown` でモデル版まで消すよう実装を変えた（版を作るのは SDK/MLflow 側 =
作った層が片付ける）。

### 計測データの訂正（透明性のため記録）

`terraform destroy <保存済みplan>` は terraform 自身が拒否する
（`Destroy can't be called with a plan file`）。この**計測器の不具合**で
`infra_events` に 0.0s の destroy 失敗が2件入ったので削除した。
基盤の摩擦ではないため比較データに残さない。ラッパ側は
「destroy plan は apply で流し、記録上の action は destroy のまま」に修正済み。
同じ経路で `Apply complete!` の行から added を読んで **destroy が 0 リソース**と
記録されていた分も 2 に訂正した（パーサ修正済み）。

## 5基盤比較への寄与

| 観点 | Databricks の位置 |
|---|---|
| 実行資源 | serverless ジョブ（**同時5タスクまで** / Free Edition） |
| Neon 到達 | **collected**（wheel に psycopg を入れておらず、egress も trusted domains 限定） |
| デプロイ | **475.7s**（毎回コンテナビルド）。scale-to-zero で idle は 0 |
| 依存の自由度 | **PyPI がそのまま使える**（Tier B では Snowflake と対極） |
| 登録の要求 | **MLflow 形式 + signature**。SDK/REST に版作成の口が無い |
| 撤退 | **残留ゼロ**。ただし SDK が作った版が destroy を止めるので teardown で消す必要がある |
| Terraform 網羅度 | 高いが **Catalog はプランの制約で作れない**（有料版なら作れる想定） |
