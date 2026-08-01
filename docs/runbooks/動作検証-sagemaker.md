# 動作検証: SageMaker AI（Phase 2）

> ## ✅ 完走（2026-08-01）— 8項目すべて達成
>
> account `123456789012` / `ap-northeast-1` / `code_revision = 7e6dc1c0`。
> RMSE `0.4368055090296257`・予測値 `4.183217948107466` はどちらも5基盤で一致。
> 到達経路は **direct**。実測値と一次記録は
> [02_sagemaker.md](../comparison/02_sagemaker.md)。
>
> **apply は1回で通った**（15リソース / 63.9s）。**destroy は2回**
> （1回目は Model Package が残って失敗）。残留は WARN 3件・FAIL 0件。
> 以下は再実行するときの手順。

| | |
|---|---|
| Tier | A（コンテナ実行型・統一単位 = 学習イメージ） |
| ENV / PLATFORM | `aws-dev` / `sagemaker` |
| リージョン | ap-northeast-1（Doppler の `AWS_DEFAULT_REGION` と一致させる） |
| 資格情報 | `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`（Doppler）。[credentials.md §3](./credentials.md) |
| 実装 | `src/platforms/sagemaker/adapter.py`（**boto3 低レベル API**）/ `docker/training/entrypoint_sagemaker.sh` |
| 着手前 | [sagemaker-phase-precheck.md](../tasks/05_done/sagemaker-phase-precheck.md) を消化してから |
| 位置づけ | 実行契約が3基盤で最も厳しい（パス固定・文字列限定）。Vertex との差が最初の比較材料 |

共通の前提・8項目の定義・停止条件は [README.md](./README.md)。以下は SageMaker 固有分のみ。

## 0. 着手前ブロッカー（2026-08-01 の準備で判明）

根拠は [sagemaker-phase-precheck.md](../tasks/05_done/sagemaker-phase-precheck.md)。
3件とも owner 判断。**先に潰さないと ① で止まるか、計測が濁る。**

| # | 内容 | 処置 | 2026-08-01 の結末 |
|---|---|---|---|
| B-1 | **tfstate バケットが無い**。`backend.tf` は partial config で、アカウントの S3 バケットは実測 0 件。`make tf-init` は `-backend-config` を渡せない | 手で作ってから `terraform init -backend-config="bucket=<state-bucket>"`（下記） | ✅ 解消（手動作成して完走） |
| B-2 | **資格情報が root キー**（`Arn ...:root`）。**投入側の permission friction が測れない**（実行ロール側は最小権限なので測れる） | そのまま進めるなら comparison 02 に「投入側は未計測」と明記（0回と書かない） | **root キーのまま完走**。最小権限 IAM への差し替えは個人ラボには過剰と判断し、**対応しない**（2026-08-01・owner 判断）。投入側 friction は**恒久的に未計測**として 02 に明記済み |
| B-3 | `budget_notification_email` 既定 `""` → **予算アラートが作られない**。常時課金のエンドポイントを通知なしで立てることになる | 通知先を Doppler（`MCML_TF_BUDGET_EMAIL`）へ置く。未解決なら apply 前に落ちる | ✅ 解消 |

**先に潰した分**（precheck 参照）: boto3 の API 契約は全一致 / quota は緩和申請不要
（training 15・spot 10・endpoint 30）/ コンテナ実行契約はローカル再現で通り
**RMSE 0.4368055090296257（基準値と一致）**/ 残留検査の絞り込み漏れは修正済み。

## 1. 着手前チェック

```bash
doppler run -- aws sts get-caller-identity     # 実行主体の確認（→ B-2）
doppler run -- aws configure get region        # ap-northeast-1
make PLATFORM=sagemaker deps-platform          # boto3（aws extra）
make test
```

- [ ] **`make PLATFORM=sagemaker deps-platform` 済み**。`make deps` は基盤 SDK を入れない（学習コンテナを太らせないため基盤ごとに別 extra）
- [ ] IAM ユーザーに **`AmazonSageMakerFullAccess` を貼っていない**
      （最小権限で通るまでの試行回数が本命の計測値。広い権限を貼ると計測が消える）
      —— **root キーのままだとこのチェックは形だけになる**（→ B-2）
- [ ] Phase 1 の comparison ページが書き終わっている（ブロック条件）→ 記入済み ✅

## 2. ① terraform apply

state 置き場は Terraform 管理外（chicken-and-egg）。**init の前に1回だけ**作る（→ B-1）。

```bash
doppler run -- aws s3api create-bucket --bucket <state-bucket> --region ap-northeast-1 \
  --create-bucket-configuration LocationConstraint=ap-northeast-1
terraform -chdir=infra/environments/aws-dev init -backend-config="bucket=<state-bucket>"

make ENV=aws-dev tf-plan && make ENV=aws-dev tf-apply   # 変数は config.yaml / Doppler から入る
terraform -chdir=infra/environments/aws-dev output -json > artifacts/aws-dev.outputs.json
```

**apply は変数なしの1回だけ**（Model / EndpointConfig / Endpoint を作る2段階目は使わない）。
この3リソースは adapter が SDK で作る。両方から作ると同名リソースを取り合う。
1段階目の outputs で `endpoint_name` 等が `null` になるのは正常
（`Settings._resolve` が捨て、dataclass 既定 `mcml-dev-endpoint` が Terraform の命名と一致する）。

`config.py` が読む outputs: `region` / `s3_bucket` / `sagemaker_execution_role_arn` /
`model_package_group_name` / `endpoint_name` / `ecr_repository_urls`（training / serving の2キー）。

### apply の実行（5基盤共通）

**Terraform 変数は `export` しない。** `env/config.yaml` の `terraform:` 節（人が決める値）と
Doppler（秘密・個人識別子）から `run_terraform.py` が組み立てる（対応表は
`src/platforms/terraform_vars.py`）。解決できない変数があれば **terraform を起動する前に**
名前を挙げて落ちる。

対話端末があるなら:

```bash
make ENV=aws-dev tf-init && make ENV=aws-dev tf-plan && make ENV=aws-dev tf-apply   # yes を入力
```

**非対話（CI・バックグラウンド・エージェント）は保存済み plan を渡す。**
素の `tf-apply` は承認プロンプトが `EOF` で落ちるうえ、**`infra_events` に偽の
failure 行が残って「apply 試行回数」を汚す**（2026-08-01 に実際に発生）。
plan 無しで非対話実行するとガードが `EXIT_USAGE` で止める:

```bash
PYTHONPATH=src doppler run -- .venv/bin/python scripts/run_terraform.py \
  plan --env aws-dev -out=/tmp/aws-dev.tfplan
PYTHONPATH=src doppler run -- .venv/bin/python scripts/run_terraform.py \
  apply --env aws-dev /tmp/aws-dev.tfplan
```

⚠️ **plan も `run_terraform.py` 経由で回す。** 素の `terraform plan` は
config.yaml / Doppler 由来の `-var` を受け取らないため、**変数の抜けた plan が
保存され、apply がそれを適用してしまう**（2026-08-01 実測: 予算アラートが落ちて
`Plan: 9 to add`。正しくは 10）。plan は `infra_events` に記録しない。

destroy も同じ（`terraform destroy <plan>` は terraform 自身が拒否するので
`plan -destroy` の成果物を渡す。記録上の action は destroy のまま保たれる）:

```bash
PYTHONPATH=src doppler run -- .venv/bin/python scripts/run_terraform.py \
  plan --env aws-dev -destroy -out=/tmp/aws-dev-destroy.tfplan
PYTHONPATH=src doppler run -- .venv/bin/python scripts/run_terraform.py \
  destroy --env aws-dev /tmp/aws-dev-destroy.tfplan
```

## 3. 配布物の準備

```bash
make docker-build && make docker-build-serving
make docker-push PLATFORM=sagemaker      # ECR ログイン + push + manifest 形式の検証まで
make distribute PLATFORM=sagemaker       # Parquet を s3://<bucket>/data/california_housing/ へ
```

⚠️ **serving は素の `docker push` だと ⑤ で落ちる。** docker 29 の buildx は
**OCI image index** で push するが `CreateModel` はこれを拒否する
（`Unsupported manifest media type application/vnd.oci.image.index.v1+json`）。
**Training は OCI でも受理される**ので、②が通っても⑤で初めて表面化する。
回避（`--oci-mediatypes=false`）と push 後の形式検証は
`scripts/push_images.sh` が持つ（手順書に書くと必ず踏むため）。

- [ ] イメージ2本が push 済み（training / serving は**別リポジトリ**）
- [ ] **学習イメージが root で動く**。`USER` を入れると `/opt/ml/model` に書けず、
      **学習を完走してから**落ちる（Dockerfile のコメント参照）
- [ ] `make docker-push` が manifest 形式の検証まで通っている
- [ ] Parquet が `s3://<bucket>/data/california_housing/` にある

## 4. ②〜⑤ フェーズ実行

```bash
make PLATFORM=sagemaker phase-train
make collect
make PLATFORM=sagemaker phase-register
make PLATFORM=sagemaker phase-deploy     # ⚠️ 常時課金
make PLATFORM=sagemaker phase-predict
```

| # | 何を見て成功と判定するか |
|---|---|
| ② | TrainingJob が `Completed`。`ml_runs` に stage=train が1行。失敗時は `/opt/ml/output/failure` の内容が `error_excerpt` に入る |
| ③ | `write_path='direct'`（ジョブ内から Neon 直 INSERT）。**届かなかった場合の回収は下記**（`make collect` だけでは終わらない） |
| ④ | Model Package Group に版が登録され、**承認状態が付く**（Vertex の alias と違い承認が1手増える） |
| ⑤ | `/invocations` が 200。`ml_runs` に stage=predict |

**③ の fallback 回収。** JSONL の退避先は成果物と同じ `/opt/ml/model` なので、
SageMaker が **`model.tar.gz` に固めてしまう**（Vertex は GCS 上に素のまま残る）。
単体オブジェクトとして拾えないので展開してから流し込む:

```bash
doppler run -- aws s3 cp "s3://$BUCKET/runs/<job-name>/output/model.tar.gz" /tmp/
tar xzf /tmp/model.tar.gz -C artifacts/fallback ml_runs.jsonl
make collect
```

この非対称性は実装で揃えず、Tier A 内の構造差として comparison に書く。

## 5. SageMaker 固有の確認

**実行契約が固定パス。** 逸脱すると起動すらしない。

```text
/opt/ml/input/data/training               入力（チャネル名 = training）
/opt/ml/input/config/hyperparameters.json ハイパーパラメータ
/opt/ml/model                             成果物（S3 へ自動回収）
/opt/ml/output/failure                    失敗理由
```

- **hyperparameters は値が文字列限定**。型を保つため JSON を `params` の1キーに畳み、
  シムが展開する。`run_id` / `attempt` も**同じ経路**で渡す（引数で渡せないため）。
- **deploy が3リソースを作る**（Model → EndpointConfig → Endpoint）。
  Vertex のように「器を先に作っておく」ができない。この差は所要時間に出るので
  stage 別所要のクエリで確認する。
- **推論 payload は文字列**（Vertex は辞書）。契約は `/ping` + `/invocations`、port 8080。
- **Spot**: `max_wait_seconds`（既定 10800）は `max_runtime_seconds`（既定 7200）より
  短くできない。中断は失敗 run として記録する。

## 6. 失敗時の切り分け

| failure_class | 典型 | 対処 |
|---|---|---|
| `permission` | execution role に S3 / ECR / CloudWatch が足りない | ポリシーを**1つずつ**足す。FullAccess で潰さない |
| `quota` | `ml.m5.large` のアカウント上限 | **本アカウントは緩和不要**（2026-08-01 実測: training 15 / spot 10 / endpoint `ml.t2.medium` 30）。この行は空になる＝それ自体が結果 |
| `container` | `/opt/ml` のパス逸脱、entrypoint の exec 形式、**非 root（uid 10001）で `/opt/ml/model` に書けない** | シムを確認。CloudWatch Logs のストリーム名は job 名。非 root 問題は precheck R-1（成果物・ml_runs 行・失敗理由が同時に消えるので最優先で見る） |
| `data` | チャネル名が `training` でない | `TRAINING_CHANNEL` と S3 prefix を確認 |
| `network` | ジョブから Neon へ届かない | VPC 無しの通常 egress で届くかの実測。JSONL fallback へ |

## 7. ⑥⑦ teardown と残留検査

```bash
make PLATFORM=sagemaker phase-teardown

# ⚠️ destroy の前に版を消す（2026-08-01 実測でここで1回落ちた）。
#   Error: Model Package Group ... cannot be deleted because it still contains Model Packages
# **SDK が作った版が、Terraform 所有の器の削除をブロックする。**
doppler run --command '
for arn in $(aws sagemaker list-model-packages --model-package-group-name mcml-dev-models \
               --query "ModelPackageSummaryList[].ModelPackageArn" --output text); do
  aws sagemaker delete-model-package --model-package-name "$arn"
done'

make ENV=aws-dev tf-destroy
```

**`phase-teardown` は Endpoint しか消せない。** teardown は同一プロセスで作った
Model / EndpointConfig の名前しか持たないので、別プロセスで叩くと2つが残る（下表）。

| kind | severity | 期待 |
|---|---|---|
| `sagemaker_endpoint` | FAIL | 0件 |
| `sagemaker_endpoint_config` | WARN | **残る**（別プロセス teardown では消えない） |
| `sagemaker_model` | WARN | **残る**（同上。SDK 所有・destroy 対象外） |
| `model_package_group` | WARN | 版を消してから destroy すれば消える |
| `ecr_repository` | WARN | Vertex の `artifact_registry` と対の項目。`force_delete=true` なので destroy で消える想定 |
| `s3_object` | WARN | 成果物を消していなければ残る。**state バケット（B-1）は Terraform 管理外なので残るのが正しい**——残留表では区別して書く |

検査は `mcml` を含む名前だけを数える（`LAB_NAME_PREFIX`）。アカウントは他用途と共有なので、
絞り込みが無いと無関係な Endpoint が FAIL＝嘘の赤になる（2026-08-01 に修正済み）。

Model Package Group が残るのは消し忘れではない。「Vertex は残留なし / SageMaker は
Model Package が残る」という**差そのものが比較結果**なので、消して揃えない。

## 8. ⑧ レポート記述

[docs/comparison/02_sagemaker.md](../comparison/02_sagemaker.md) を埋める。
Vertex との差（器を先に作れない / 文字列限定 / 承認1手 / 残留の質）を明示的に1節にする。

**2026-08-01 実施済み（8/8 達成）。** 上記の §0・§3・§7 の注記はすべてその実測に基づく。
再実行するときは、②で1回・⑤で1回・⑥で1回落ちた地点（非 root / OCI マニフェスト /
版の削除順序）が潰れているかを先に確認する。
