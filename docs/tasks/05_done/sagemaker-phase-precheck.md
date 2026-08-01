# SageMaker Phase 2 着手前確認

> ✅ **消化済み（2026-08-01）**。該当 Phase は完走し、実測は `docs/comparison/` が正本。
> このファイルは着手前に何を潰したかの記録として残す。

Weight Class: Light（調査 + 検査側の1修正）

## Goal

Phase 2（SageMaker AI）の実行前に、**クラウドに触らずに確定できるものを全部潰す**。
Snowflake で「クラウドで初めて分かる」を4回やった反省（→ Databricks precheck）を
Tier A 側にも適用する。実測日 **2026-08-01** / HEAD `94a28c4`。

## Value

Phase 2 は参照実装ありで5〜7h の見積り（[作業順番.md](./作業順番.md)）。
着手後に止まる要因は「準備で潰せたもの」と「クラウドでしか分からないもの」に分かれる。
前者を残したまま始めると、常時課金のエンドポイントを抱えたまま調べ物をすることになる。

## Scope

- ローカルで判定できる項目の実測（SDK 契約 / コンテナ契約 / 設定解決 / 検査側）
- read-only の AWS API で判定できる項目（資格情報 / quota / 既存リソース）
- 実測で判明した runbook の誤りの訂正

## Non-scope

- terraform apply 以降（フェーズ本体）
- IAM 最小権限化そのもの（owner-only。2026-08-01 に「対応しない」で確定）
- 非 root 問題の予防的なイメージ変更（下記 R-1 の owner 判断待ち）

---

## 着手前ブロッカー（**未解決**・先に潰さないと ① で止まる）

### B-1. tfstate バケットが無い —— runbook の `tf-init` はそのままでは通らない

`infra/environments/aws-dev/backend.tf` は **partial config**（bucket を書いていない）。
GCP 側（`gs://example-gcp-project-tfstate` 直書き）と違い AWS には state 用バケットがまだ無く、
実測で **アカウントの S3 バケットは 0 件**（`aws s3api list-buckets` → 空）。

```
Makefile: tf-init: terraform -chdir=$(TF_DIR) init      # -backend-config を渡す口が無い
```

つまり runbook §2 の1行目が失敗する。**chicken-and-egg（state 置き場は Terraform 管理外）**
なので、先に手で作ってから init に渡す。バケット名は owner 判断（案:
`mcml-dev-123456789012-tfstate`。GCP の `<project>-tfstate` と同じ形）。

```bash
doppler run -- aws s3api create-bucket --bucket <state-bucket> --region ap-northeast-1 \
  --create-bucket-configuration LocationConstraint=ap-northeast-1
terraform -chdir=infra/environments/aws-dev init -backend-config="bucket=<state-bucket>"
```

**このバケットは残留検査に載る**（`_is_lab_resource` は `mcml` を含む名前に一致）。
destroy 後も残るのが正しいので、comparison の残留表では「Terraform 管理外の state 置き場」
として区別して書く（Vertex 側の `example-gcp-project-tfstate` も同じ扱い）。

### B-2. 資格情報が root アクセスキーのまま —— 計測軸の半分が成立しない

```
doppler run -- aws sts get-caller-identity
→ "Arn": "arn:aws:iam::123456789012:root"   （2026-08-01 実測。root キーのまま使う判断で確定 → `docs/decisions/decision-log.md`）
```

runbook §1 のチェック「IAM ユーザーに `AmazonSageMakerFullAccess` を貼っていない」は
**形式上は満たすが意味が無い**（root は最初から全部持っている）。

本ラボの permission friction は2層ある。**この状態で測れるのは下の層だけ**:

| 層 | 誰の権限か | root キーでの計測 |
|---|---|---|
| 投入側（`create_training_job` / `create_model_package` / `create_endpoint`） | 手元の呼び出し主体 | ❌ **測れない**（root は落ちない） |
| 実行側（ジョブ・エンドポイントが S3 / ECR / CloudWatch を触る） | `sagemaker_execution_role_arn`（Terraform の最小権限ポリシー） | ✅ 測れる |

実行側は `infra/modules/aws/main.tf` が Sid 分割の最小権限で `AmazonSageMakerFullAccess` を
貼っていないので、**②の失敗試行という一次データはこの層から取れる**。

owner 判断（→ 下記「Stop / Ask Owner If」）。そのまま進める場合は、
**comparison 02 に「投入側の friction は未計測」と明記する**（0回と書かない）。

### B-3. 予算ガードが既定で作られない

`budget_notification_email` の既定は `""` → `aws_budgets_budget` は `count = 0`。
**常時課金のエンドポイントを立てるフェーズで、通知が1つも無い状態**になる。
Vertex（Phase 1）は budget を作って回している。

```bash
TF_VAR_budget_notification_email="<owner mail>" make ENV=aws-dev tf-apply
```

---

## 実測で潰した分（クラウド不要）

### boto3 の API 契約 —— **全一致**（断線なし）

Databricks で `model_versions.create` が存在しなかった型の事故を先に潰す。
botocore のサービスモデル（**boto3 1.43.62** / `.venv` に導入済み）と adapter の
呼び出しを突き合わせた。

| adapter の呼び出し | 実測 |
|---|---|
| `create_training_job`（`AlgorithmSpecification.ContainerEntrypoint` / `Environment` / `EnableManagedSpotTraining` / `StoppingCondition.MaxWaitTimeInSeconds` / `HyperParameters` / `Tags`） | ✅ 全キーが input shape に存在 |
| `create_model_package`（`InferenceSpecification` / `ModelApprovalStatus`） | ✅ 一致 |
| `create_model` / `create_endpoint_config` / `create_endpoint` / `update_endpoint` | ✅ 一致 |
| `sagemaker-runtime.invoke_endpoint`（`Body` / `ContentType` / `Accept`） | ✅ 一致 |
| waiter `training_job_completed_or_stopped` / `endpoint_in_service` | ✅ `client.waiter_names` に実在 |
| `ml.m5.large`（training）/ `ml.t2.medium`（endpoint） | ✅ enum に実在（`ml.t3.medium` は**無い**ので変更しない） |

### quota —— runbook §6 の「新規アカウントは 0」は**本アカウントでは該当せず**

```
doppler run -- aws service-quotas list-service-quotas --service-code sagemaker
→ ml.m5.large for training job usage        15
   ml.m5.large for spot training job usage   10
   ml.t2.medium for endpoint usage           30
```

上限緩和申請は不要。「申請に要した時間も比較材料」の行は Phase 2 では空になる（＝それ自体が結果）。

### 実行契約のリハーサル（Docker でローカル再現） —— **通る**

`/opt/ml` を模した固定パスを bind mount し、**実イメージ**で
`ContainerEntrypoint = ["bash", "/app/entrypoint_sagemaker.sh"]` を再現した。

| 見たもの | 結果 |
|---|---|
| `hyperparameters.json` の3値（`params` / `run_id` / `attempt`）の復元 | ✅ JSON 1キー畳み込みが往復する。`run_id` 空でも値がずれない |
| `/opt/ml/model` への成果物 | ✅ `model.txt` / `metrics.json` / `feature_importance.csv` / `run.json` |
| ジョブ内テレメトリ（`platforms.neon.job_record`） | ✅ Neon 不達 → JSONL 退避 → `write_path=collected` / `attempt` を hyperparameters 由来で保持 |
| **metric parity** | ✅ RMSE **0.4368055090296257**（Phase 0 基準値と完全一致） |

metric parity をコンテナ契約の中で先に確認したので、②が動いた後に RMSE がずれたら
**原因はコードでも依存でもなく基盤側**と切り分けられる。

再現コマンド（`$SB` は任意の作業ディレクトリ。`/opt/ml/model` と `/opt/ml/output` は
コンテナのユーザ 10001 が書ける必要がある → R-1）:

```bash
docker run --rm --network none -v "$SB/opt/ml:/opt/ml" \
  --entrypoint bash mcml-training:latest /app/entrypoint_sagemaker.sh
```

### 設定解決 —— 2段階 apply と衝突しない

- `endpoint_name` / `endpoint_config_name` / `sagemaker_model_name` は
  **1段階目の outputs では `null`**（`local.deploy_enabled = false`）。
  `Settings._resolve` は `None` / `""` の outputs を捨てるので、
  `SageMakerConfig.endpoint_name` の既定 `mcml-dev-endpoint` が生き、
  Terraform 側の命名（`${project_name}-${environment}-endpoint`）と一致する。**事故なし**。
- したがって **2段階目の apply（`model_data_url` を渡す）は Phase 2 では実行しない**。
  Model → EndpointConfig → Endpoint は adapter（SDK）が作る。両方から作ると
  同名リソースを取り合う。runbook §2 が変数なしの1回だけなのはそのため。
- `training_image_uri` / `serving_image_uri` は `ecr_repository_urls`（training / serving の
  2キー）+ `common.image_tag` から組む。**outputs が無いと必須フィールド欠落で落ちる**
  （エラー文に復旧手順が出る設計）。

### 検査側の穴 → **修正済み**（本 task のコミット）

`check_residual.check_sagemaker` に **`LAB_NAME_PREFIX` の絞り込みが無かった**
（Vertex 側は同じ穴でバケット 12 件を誤検出済み）。AWS アカウントは他用途と共有なので、
無関係な Endpoint が **FAIL = 嘘の赤**（撤退失敗の誤報）に、無関係なバケットが
「SageMaker の残留」として比較表に載る。4項目すべてに絞り込みを追加した。

あわせて **ECR の列挙を追加**（`ecr_repository` / WARN）。docstring は「ECR を列挙する」と
書いていたのに実装が無く、Vertex の `artifact_registry` と対にならない
= 「Vertex にはレジストリの残留行があるが SageMaker には無い」という**見かけの差**が
比較表に出るところだった。テスト3件を追加（うち2件は修正前に RED を確認）。

### その他

- `make test` → **484 passed / 5 skipped**（skip は Phase 0 プレースホルダ）
- `terraform validate`（aws-dev）→ Success。provider `~> 5.0`
- Phase 1 の comparison ページ（runbook §1 のブロック条件）→ [01_vertex.md](../../comparison/01_vertex.md) 記入済み ✅
- boto3 は `.venv` に導入済み（`make PLATFORM=sagemaker deps-platform` 相当は完了）

---

## クラウドでしか分からない残り（優先度順）

### R-1. 学習イメージが**非 root**（uid 10001）—— ②の最後で落ちうる

`docker/training/Dockerfile` は `USER mlrunner`（uid 10001）。SageMaker が
`/opt/ml/model` と `/opt/ml/output` を **root 所有 755 で作る**場合、
**学習が全部終わった後の書き出しで落ちる**。ローカルで同条件（別ユーザ所有の 755）を
再現したときの実際の出力:

```
[LightGBM] [Fatal] Model file /opt/ml/model/model.txt is not available for writes
job_record: JSONL への退避にも失敗: [Errno 13] Permission denied
/app/entrypoint_sagemaker.sh: line 35: /opt/ml/output/failure: Permission denied
```

**3つ同時に潰れるのが厄介**: 成果物が出ない・ml_runs 行が残らない・失敗理由も書けない
（= `failure_class` が推測になる。シム自身のコメントが警告している状態そのもの）。

AWS ドキュメントは BYOC の実行ユーザについて所有権を明示していない（2026-08-01 参照）。
判定は**最初の spot ジョブ1本**が最も安い（数分・数円）。落ちた場合の処置は owner 判断:

- 案A: 学習イメージから `USER` を外す（Tier A 3基盤共通イメージなので Vertex / Azure ML にも影響。
  ただし両者は root でも動く）
- 案B: SageMaker のときだけ別タグでビルド → **同一イメージという Tier A の同一性担保が崩れる**ので不可
- 案C: 落ちた事実を比較材料として記録し、`ml.m5.large` の失敗 run を残したまま A へ

### R-2. ③ の fallback 回収は `make collect` だけでは終わらない

`job_record` の JSONL 退避先は **`--output` と同じディレクトリ = `/opt/ml/model`**。
SageMaker はそこを **`model.tar.gz` に固めて** `s3://<bucket>/runs/<job>/output/` へ置くので、
Vertex（GCS のディレクトリにそのまま残る）と違い **単体のオブジェクトとして拾えない**。

```bash
# Neon 直が不達だった場合の実際の回収手順
doppler run -- aws s3 cp "s3://$BUCKET/runs/<job-name>/output/model.tar.gz" /tmp/
tar xzf /tmp/model.tar.gz -C artifacts/fallback ml_runs.jsonl
make collect
```

runbook §4 の③行を訂正済み。**この非対称性（成果物と同じ場所しか書けない）自体が
Tier A 内の構造差**なので、実装を変えて揃えず comparison に書く。

### R-3. その他（順に）

- Managed Spot の中断が実際に起きるか（`max_wait 10800` / `max_runtime 7200`）
- `create_model_package` の `ModelApprovalStatus="Approved"` が作成時に受理されるか
  （承認が1手増えること自体は Vertex との差として記録済み。**受理されなければ承認は別 API**）
- エンドポイント起動の所要時間（Vertex は 1356s で突出。SageMaker は3リソース積み上げ）
- ECR の `scan_on_push` が push を遅らせるか（比較の所要時間に混ざる）

---

## Done

- 上記「実測で潰した分」が本 task に記録され、runbook に反映されている ✅
- B-1 / B-2 / B-3 に owner の回答があり、①の前に処置されている
- R-1 の判定結果（非 root で通る / 通らない）が本 task に追記されている

## Evidence

- Evidence Level 2〜3（実コマンド出力・実テスト・実コンテナ実行）。上記の値はすべて実行結果。
- B-2 は Evidence Level 4（本番クレデンシャル）—— 差し替えるなら
  root キーのまま使う判断で確定（`docs/decisions/decision-log.md`）。

## Stop / Ask Owner If

- **owner-only**: B-1 の state バケット作成（AWS リソース作成）、B-2 の IAM 判断、
  B-3 の通知先メール。エージェントが独断で実行しない。
- R-1 が発生したら、イメージを直す前に owner へ（Tier A の同一性担保に触れるため）。
- 同じ検証が2回、違う理由で失敗したら止める（`docs/specs/runtime-protocol.md`）。

## 出典

- 前例: [databricks-phase-precheck.md](./databricks-phase-precheck.md)（同じ形で Phase 3 を通した）
- 実装側の正本: [仕様準拠監査-2026-08-01.md](./仕様準拠監査-2026-08-01.md)
