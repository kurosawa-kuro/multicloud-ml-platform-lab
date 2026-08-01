# SageMaker AI

> 実測日 2026-08-01 / account `123456789012` / region `ap-northeast-1` /
> `code_revision = 7e6dc1c00f96dd716d82299981c017900e29df5a`
> 実行手順と合否判定: [../runbooks/動作検証-sagemaker.md](../runbooks/動作検証-sagemaker.md)
> 着手前に潰した分: [../tasks/05_done/sagemaker-phase-precheck.md](../tasks/05_done/sagemaker-phase-precheck.md)

Tier: A（コンテナ実行型・統一単位 = 学習イメージ）

## 構造仮説（着手前の予想・実測で検証する）

- 実行契約が3基盤で最も厳しい（/opt/ml の固定パス・hyperparameters.json・port 8080）
- Terraform では Endpoint まで書けるが、Model Registry の登録・承認が SDK に残る
- 既存資産は boto3 の S3 I/O のみ。SageMaker SDK・BYOC 契約は新規

| 予想 | 実測 | 差分 |
|---|---|---|
| 実行契約が最も厳しい | **そのとおり。ただし厳しさの中身が予想と違った。** 固定パス・文字列限定は準備段階でローカル再現できた（詰まらなかった）。実際に2回止めたのは **`/opt/ml` の書き込み権限**と **ECR のマニフェスト形式**——どちらもコードではなく**配布物の作り方**の契約 | 「パスの契約」ではなく「**イメージの契約**」が本体だった |
| Terraform で Endpoint まで書ける | **書けるが使えない。** Endpoint ← EndpointConfig ← Model ← 学習成果物の依存があり、2段階 apply（学習後に `model_data_url` を渡す）でしか作れない。本フェーズは SDK 側に寄せ、Terraform は器を作っていない | Vertex の「器を先に作る」に相当する手が**構造的に存在しない** |
| 登録・承認が SDK に残る | **そのとおり。** グループ（器）は Terraform、版の登録と承認は SDK。承認は `CreateModelPackage` の `ModelApprovalStatus="Approved"` で**作成と同時に付けられた**（別 API の呼び直しは不要） | 承認は「1手増える」だけで待ちは発生しない |
| —（予想していなかった） | **SDK が作った版が Terraform の destroy をブロックした。** Model Package Group（Terraform 所有）は、中に版（SDK 所有）が残っていると削除できない | **所有者の違うリソース間に削除順序の依存**がある。Vertex には無かった結合 |
| —（予想していなかった） | **deploy が Vertex の 1/11（122.4s vs 1356.0s）。** register は 1/190（0.78s vs 154.7s） | Tier A の律速は「エンドポイント起動」だが、**その速さは基盤ごとに1桁違う** |

## 完了条件8項目

| # | 項目 | 結果 |
|---|---|---|
| ① | terraform apply | ✅ 15 resources / 63.9s（`infra_events` に記録） |
| ② | 学習ジョブ成功（失敗試行も記録済み） | ✅ `Completed`。**attempt=2**（1回目は失敗、下記1） |
| ③ | Neon へメトリクス到達（direct / collected） | ✅ **direct**（ジョブ内から直接 INSERT。全8行とも direct） |
| ④ | モデル登録 | ✅ `mcml-dev-models` v1 / v2、いずれも `Approved` / 0.78s |
| ⑤ | 1件オンライン推論 | ✅ 0.37s / 予測値 `4.183217948107466`（**Vertex と完全一致**） |
| ⑥ | terraform destroy | ✅ ただし **attempt=2**（1回目は Model Package が残っていて失敗。下記3） |
| ⑦ | 残留リソース記録 | ✅ **WARN 3件** / 課金が続くもの（FAIL）は 0件 |
| ⑧ | 比較レポート1ページ記述（本ページ） | ✅ |

## 実測値

数値は Neon の SELECT（`sql/comparison_queries.sql`）から起こした。手で数えていない。

| 指標 | 値 | 出典 |
|---|---|---|
| RMSE | **0.4368055090296257** | metric parity |
| code_revision | `7e6dc1c0…`（全 run で1種類） | metric parity |
| 最小権限で通るまでの試行回数 | train **2** / register 1 / deploy **2** / predict 1 | permission friction |
| failure_class の内訳 | `sdk` × 1（**実態は container**・下記1）/ `container` × 1。**iam / permission は0回** | permission friction |
| stage 別所要時間 | train 5.0s（ジョブ内）/ register 0.78s / **deploy 122.4s** / predict 0.37s / teardown 0.65s | stage 別所要 |
| Neon 到達経路 | **direct 8 / collected 0** | 到達経路内訳 |
| Terraform でカバーできた範囲 | S3（暗号化・公開遮断・ライフサイクル・バケットポリシー）・ECR 2本 + ライフサイクル・IAM Role/Policy・Model Package Group の**器**・Budget = 15 | 手記述 |
| SDK/CLI に残った範囲 | Training Job 投入・版の登録と承認・Model → EndpointConfig → Endpoint・推論・Endpoint 削除 | 手記述 |
| destroy 後の残留 | **3件**（EndpointConfig / Model / state バケット）。課金継続なし | teardown 品質 |
| アイドル時課金の構造 | Endpoint が `InService` の間だけ課金（`ml.t2.medium` × 1）。**器だけ作って待機**ができないので、Vertex のような「無課金の器」状態が存在しない | 手記述 |

補足1: train の所要は Vertex と同じく**2つの意味**がある。

- **5.0s** = ジョブの中で測った学習時間（`ml_runs` に載るのはこちら）
- **121.1s** = adapter から見た投入→完了（Spot の待ち + プロビジョニング + イメージ pull 込み）

補足2: **Managed Spot が効いた。** `TrainingTimeInSeconds=54` に対し
`BillableTimeInSeconds=19`（約65%減）。中断は発生しなかった。

補足3: quota は緩和申請不要だった（training `ml.m5.large` 15 / spot 10 /
endpoint `ml.t2.medium` 30）。「申請に要した時間」は本アカウントでは0。

補足4（`code_revision` の正確な意味）: 記録値 `7e6dc1c0…` は**イメージビルド時の HEAD**。
学習コードそのもの（`src/core/ml`）はこの commit の内容と完全に一致するが、
`docker/training/Dockerfile` の `USER` 削除（下記1の対処）は**ビルド時点で未コミット**で、
後続の commit に入っている。**学習結果を左右する要素は含まれていない**（RMSE は他4基盤と一致）が、
「イメージ = commit のツリー」ではない点を明示しておく。

## 詰まった点（一次記録）

失敗の記録がこのプロジェクトの本体。うまくいった手順よりも、何回何を直したかを残す。

| # | 事象 | 分類 | 対処 |
|---|---|---|---|
| 1 | 学習を完走した直後に `[LightGBM] [Fatal] Model file /opt/ml/model/model.txt is not available for writes` | `container` / attempt 1（**記録上は `sdk`**） | SageMaker は `/opt/ml/{model,output}` を **root 所有で作ってからコンテナを起動する**。学習イメージが `USER mlrunner`（uid 10001）だったため書けない。`USER` を外して root 実行へ（Vertex / Azure ML は `/opt/ml` を使わないので影響なし） |
| 2 | 同時に `/opt/ml/output/failure: Permission denied` | 計測の劣化 | 失敗理由を書けないので `DescribeTrainingJob` の `FailureReason` が **空**（`AlgorithmError: , exit code: 1`）になり、`failure_class` が実態の `container` ではなく exit code 由来の `sdk` に落ちた。**成果物・ml_runs 行・失敗理由の3つが同時に潰れる** |
| 3 | `CreateModel` が `Unsupported manifest media type application/vnd.oci.image.index.v1+json` | `container` / deploy attempt 1 | docker 29 の buildx が **OCI image index** で push するため。`docker buildx build --provenance=false --sbom=false --output type=image,oci-mediatypes=false,push=true` で Docker schema2 に切り替えて解決。**同じ形式の学習イメージは Training が受理していた** |
| 4 | `terraform destroy` が `Model Package Group ... cannot be deleted because it still contains Model Packages` | 削除順序 / destroy attempt 1 | SDK が作った版を先に `delete-model-package` してから destroy を再実行 |
| 5 | `terraform init` が backend の bucket 未指定で通らない | 準備 | `backend.tf` が partial config で、AWS 側に state バケットが無かった（GCP は既存を直書き）。手で作って `-backend-config="bucket=..."` を渡す。**この1手が Tier A で AWS だけに要る** |
| 6 | 残留検査に `sagemaker_model` が無かった | 実装漏れ | teardown は**同一プロセスで作った** Model / EndpointConfig しか消せない設計なので、別プロセスで叩くと Model が残る。検査項目に無ければ「残留ゼロ」の嘘になるところだった（Vertex の `registered_model` と同型の穴）。検査を追加 |
| 7 | 残留検査が本ラボ以外のリソースを数えうる状態だった | 実装漏れ（着手前に発見） | `check_sagemaker` に `LAB_NAME_PREFIX` の絞り込みが無く、無関係な Endpoint が **FAIL＝嘘の赤**になる。Vertex が誤検出12件を出した穴と同型。着手前に修正（[precheck](../tasks/05_done/sagemaker-phase-precheck.md)） |

**1・3 は準備段階で予告できていた/できなかったの差が出た。**
1（非 root）はローカルの Docker で同条件を再現して**着手前に予告**し、実クラウドで的中した。
3（マニフェスト形式）は**予告できなかった**——ローカルの docker run では再現せず、
レジストリへ push して初めて現れる契約だったため。
**「イメージが動くこと」と「基盤がイメージを受け取れること」は別の検査が要る。**

### permission friction の計測範囲（重要な限界）

`iam` / `permission` の失敗は **0回**だが、これは2つの理由の合成であり、
**Vertex の0回とは意味が違う**。

| 層 | 誰の権限か | 本フェーズでの計測 |
|---|---|---|
| 投入側（`create_training_job` / `create_model_package` / `create_endpoint`） | 手元の呼び出し主体 | ❌ **未計測**。資格情報が **root アクセスキー**のため、権限で落ちること自体が起こらない |
| 実行側（ジョブ・エンドポイントが S3 / ECR / CloudWatch を触る） | `mcml-dev-sagemaker-exec`（Sid 分割の最小権限・`AmazonSageMakerFullAccess` 不使用） | ✅ 計測済み。**1回も足さずに通った** |

実行側が一発で通ったのは、移植元（aws-samples）が FullAccess を貼っていたのに対し
**必要な4系統（S3 / ECR 認証 / ECR pull / CloudWatch Logs + Metrics）を先に列挙して置いた**ため。
Vertex 側の「既知の正解を先に置いた場合の下限」と同じ性質の値とみなす。
**投入側は測れていない**（資格情報が root キーのため、何を試しても通ってしまう）。
最小権限 IAM への差し替えは個人ラボには過剰と判断してやらないので、
**この欄は恒久的に「未計測」**。0回と書かない。

## 撤退時に残ったもの

```
$ python scripts/check_residual.py --platform sagemaker
[WARN] sagemaker/sagemaker_endpoint_config: mcml-dev-config-99fc40091fea
[WARN] sagemaker/sagemaker_model: mcml-dev-model-99fc40091fea
[WARN] sagemaker/s3_object: s3://mcml-dev-123456789012-tfstate
-- FAIL/ERROR 0 件 / 全 3 件
```

検査範囲: `sagemaker_endpoint`（FAIL 判定）/ `sagemaker_endpoint_config` /
`sagemaker_model` / `model_package_group` / `ecr_repository` / `s3_object`（WARN）。

| 何が | 残ったか | 誰が作ったか |
|---|---|---|
| SageMaker Endpoint | ❌ 消えた（課金停止） | SDK |
| S3 バケットと成果物 | ❌ 消えた（`force_destroy`） | Terraform |
| ECR 2本とイメージ | ❌ 消えた（`force_delete`） | Terraform |
| Model Package Group | ❌ 消えた（**ただし版を手で消してから**） | Terraform（器）+ SDK（版） |
| **EndpointConfig** | ✅ **残った** | **SDK のみ** |
| **Model** | ✅ **残った** | **SDK のみ** |
| **state バケット** | ✅ **残った**（正しい） | **Terraform 管理外**（chicken-and-egg） |

**Vertex と同じ規則性が再現した: Terraform が所有していれば消え、SDK だけで作ったものが残る。**
違いは**残る個数**で、Vertex は 1件（登録モデル）、SageMaker は 2件（Model / EndpointConfig）。
deploy が3リソースを積み上げる構造がそのまま残留の数になっている。

state バケットの1件は残って正しい（Terraform 自身の置き場）。**GCP は既存バケットへ相乗りできたが、
AWS は Phase 2 のために自前で作る必要があった**——「①の前に手作業が1つ増える」差として記録する。

### 掃除（測定 → 記録 → 掃除）

`infra_events` に記録済みなので、残留オブジェクトは記録後に消してよい（次フェーズの
残留測定に前回の残骸が混ざる方が害）。判断根拠は Vertex と同じ:

| 観点 | 実測 | 結論 |
|---|---|---|
| 費用 | 課金実体（Endpoint）は削除済み。Model / EndpointConfig はメタデータのみ | 残しても ¥0 |
| 再利用による時間短縮 | 参照先の `ModelDataUrl`（S3）も `Image`（ECR）もバケット/リポジトリごと消えており**デプロイ不能な空参照**。仮に生きていても短縮対象は register の 0.78s のみ | 短縮ゼロ |
| 比較材料 | `infra_events` に記録済み | 物としては不要 |

**state バケットは消さない**（次フェーズの state 置き場。Terraform の管理外なので destroy 対象でもない）。

掃除後（2026-08-01 実施）:

```
$ python scripts/check_residual.py --platform sagemaker
[WARN] sagemaker/s3_object: s3://mcml-dev-123456789012-tfstate
-- FAIL/ERROR 0 件 / 全 1 件
```

## Vertex AI との差（Tier A 内比較）

| | Vertex AI | SageMaker AI |
|---|---|---|
| ① apply | 17 resources / 30.4s | 15 resources / **63.9s** |
| 事前準備 | state バケットは既存を流用 | **state バケットを自前で作る手が1つ増える** |
| ② 学習 | 10.0s（ジョブ内） | 5.0s（ジョブ内）。**Spot で課金 65%減** |
| 実行契約 | `AIP_MODEL_DIR` 等の env。出力先は自分で作る | `/opt/ml` 固定・**root 所有**。非 root イメージは通らない |
| イメージ形式 | OCI index のまま通る | **CreateModel は Docker schema2 のみ**（Training は OCI も可） |
| ③ Neon 到達 | direct | direct（**Tier A で2基盤連続**） |
| ④ 登録 | 154.7s | **0.78s**（1/190）。承認は作成時に同時付与 |
| ⑤ デプロイ | **1356.0s** | **122.4s**（1/11） |
| 器を先に作る | できる（無課金で待機可） | **できない**（Model ← 成果物の依存） |
| ⑥ destroy | 16 resources / 20.2s・1回で成功 | **1回目失敗**（SDK 所有の版が Terraform 所有の器の削除を阻む） |
| ⑦ 残留 | WARN 1件（登録モデル） | WARN 3件（Model / EndpointConfig / state バケット） |
| 推論 payload | 辞書 | 文字列（`Body=json.dumps(...)`）。**予測値は完全一致** |

**Tier A の中でも「同一イメージを配って同じコードを動かす」までの手数が違う。**
Vertex は push すれば動いたが、SageMaker は **書き込みユーザとマニフェスト形式**という
2つのイメージ側の契約を追加で満たす必要があり、それぞれ1回ずつ失敗して学習した。
一方で**動き出してからは速い**（登録 1/190・デプロイ 1/11）。
撤退コストも「速いが順序に厳しい」——削除の依存を人が知っている前提の API になっている。
