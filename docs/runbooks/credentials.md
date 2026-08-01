# クレデンシャル台帳（キー名と用途のみ・値は書かない）

> 根拠: [01_requirements.md](../01_requirements.md) / [02_architecture.md](../02_architecture.md)
> 方針: 値は絶対にここへ書かない（`.claude/rules/security.md`）。共有・長寿命の秘密は Doppler、
> ローカル限定の秘密は `env/secret.yaml`（gitignore）、非機密の ID 類は `env/config.yaml` に置く。
> 命名規則（2026-08-01 再設計・3 層）: **L1** SDK / CLI / provider が固定名で読む値はその名前のまま
> （`AWS_ACCESS_KEY_ID` `GOOGLE_CLOUD_PROJECT` `AZURE_TENANT_ID` `SNOWFLAKE_*` `DATABRICKS_*`
> `NEON_API_KEY` `KAGGLE_API_TOKEN` `DOPPLER_TOKEN`）／ **L2** 固定名を持たない値は
> `<SERVICE>_<QUALIFIER…>_<TYPE>`（`NEON_MULTICLOUD_*_URI`）／ **L3** 本リポのコードだけが読む
> 入力値は `<REPO>_<AREA>_<FIELD>`（`MCML_TF_*`）。機能カテゴリ接頭辞（`AI_`/`DB_`/`ML_`/`NOTIFY_`）は
> 使わない。詳細は doppler-key名の再設計.md。

## 0-a. 実識別子を repo に書かない（2026-08-02 追加）

秘密ではないが**実在を指す識別子**（クラウドのアカウント ID / プロジェクト ID /
ワークスペース URL / DB エンドポイント / 個人のメール・ユーザー名）も repo に書かない。
公開時にそのまま出る上、git 履歴からは後から消せないため。

正本は**すべて Doppler にある**（`AWS_ACCOUNT_ID` / `GOOGLE_CLOUD_PROJECT` /
`SNOWFLAKE_ACCOUNT` / `SNOWFLAKE_USER` / `DATABRICKS_HOST` / `NEON_MULTICLOUD_*_URI` /
`MCML_TF_BUDGET_EMAIL`）。repo 側は用途別に次を使う。

| 置き場 | 書き方 |
|---|---|
| テスト・fake | 明らかに偽と分かる値（`example-gcp-project` / `123456789012` / `ABCDEFG-HI12345`）。**Doppler を読ませない**——`make test` がネットワークと認証を要求するようになり、テストの密閉性が壊れる |
| docs | `<project-id>` `<owner-email>` 等のプレースホルダ。実測の**意味**（root ARN で実行した等）は残し、値だけ伏せる |
| Terraform | `default` に実値を書かない。`VAR_SPECS`（`terraform_vars.py`）経由で Doppler から渡す。backend の bucket も直書きせず partial config にし `-backend-config` で渡す |

偽値を選ぶときは**ラボ自身の接頭辞 `mcml` と衝突させない**。`check_residual.py` の
`LAB_NAME_PREFIX = "mcml"` に引っかかり、「無関係リソースを誤検出しない」テストの意図が
黙って壊れる（2026-08-02 に実際に踏んだ）。

## 0. 全体像

必要になる認証情報は「誰が何をするか」で 4 系統に分かれる。

| 系統 | 使うコンポーネント | 必要な操作 |
|---|---|---|
| IaC (Terraform) | `infra/modules/{gcp,aws,azure,neon}` | IAM / Storage / Registry / Endpoint 基盤 / Neon の apply・destroy |
| ML 実行 (SDK/CLI) | `src/{vertex,sagemaker,azureml}` | 学習ジョブ投入・モデル登録・デプロイ・推論 1 件 |
| コンテナ push | `docker/training/` のビルド成果物 | Artifact Registry / ECR / ACR への push |
| 計測・記録 | `src/core/telemetry/recorder.py`, `scripts/check_residual.py`, cost 収集 | Neon への書き込み、各クラウドの残留リソース列挙・課金読み取り |

Phase 順（1: Vertex → 2: SageMaker → 3: Databricks → 4: Azure 条件付き → 5: Snowflake）に
合わせて段階的に用意すればよく、**初回に全部揃える必要はない**。
各 Phase 着手時に該当セクションと対応する動作検証 runbook（[README.md](./README.md)）を開く。
Neon は計測 DB として全 Phase 共通（§1）。

## 1. 共通（Phase 0 から必要）

| キー名 | 用途 | 使用箇所 | 保管先 |
|---|---|---|---|
| `NEON_MULTICLOUD_POOLED_URI` | 計測 DB（`ml_runs` / `infra_events` / `cost_snapshots`）への接続文字列（pooled endpoint・常用） | `src/platforms/neon/connection.py`, `scripts/check_residual.py`, cost 収集 | Doppler |
| `NEON_MULTICLOUD_DIRECT_URI` | 同 DB の direct endpoint。DDL・マイグレーション用（`sql/schema.sql` の適用先） | migration | Doppler |
| `NEON_API_KEY` | Neon API。Terraform neon provider（`infra/modules/neon`）でのプロジェクト・DB 管理、接続文字列の取得 | Terraform, `scripts/mcp/neon.sh` | Doppler |

- 非機密: Neon プロジェクト名・DB 名・ブランチ名 → `env/config.yaml`
  （実体は `project=multicloud-ml-lab` / `branch=production` / `db=mlcompare` / `region=aws-ap-southeast-1`）
- Doppler CLI 自体はローカルではログインセッションで動く。非対話ランナーに載せる場合のみ
  `DOPPLER_TOKEN`（service token）が別途必要。現時点では不要（Doppler には登録済み）。

### 1-a. Terraform 入力（`MCML_TF_*` / 修正04 で export を廃止）

`env/config.yaml` に置けない Terraform 入力（個人のメール・請求先）。コミットされる場所に
書けないので Doppler が正本。解決は `src/platforms/shared/terraform_vars.py` の `VAR_SPECS` で、
**未解決なら terraform を起動する前に名前を挙げて落ちる**（渡し忘れがクラウド側の失敗として
初めて表面化するのを防ぐ。Databricks は4つ渡さないと ① で落ちていた）。

| キー名 | 用途 | 使う環境 | 保管先 |
|---|---|---|---|
| `MCML_TF_VERTEX_SUBMITTER_EMAIL` | actAs binding の付与先 | gcp-dev | Doppler |
| `MCML_TF_BILLING_ACCOUNT_ID` | 予算アラートの請求先（修正09 で必須化） | gcp-dev | Doppler |
| `MCML_TF_BUDGET_EMAIL` | 予算アラートの通知先 | aws-dev / azure-dev | Doppler |
| `MCML_TF_DBX_JOB_PRINCIPAL` | ジョブ実行プリンシパル | dbx-dev | Doppler |
| `MCML_TF_SF_GRANT_TO_USER` | ロールの付与先ユーザー | sf-dev | Doppler |

**Vertex AI Experiments への複写（秘密ではない・Doppler に置かない）**:
`VertexConfig.experiment`（config.yaml の `platforms.vertex.experiment`、または
env 上書き `MCML_VERTEX_EXPERIMENT` —— 既存の `MCML_<PLATFORM>_<FIELD>` 規約そのもの）。
**未設定なら複写しない**（既定 OFF）。有効化するとクラウドへの書き込みが増えるため、
config.yaml で常時 ON にしていない。単一基盤の関心なので共通層（factory / core）には
一切現れず、実装は `VertexAdapter._tracked` の override に閉じている
（`src/platforms/vertex/experiment_observer.py` の docstring）。

`GOOGLE_CLOUD_PROJECT` も 2026-08-02 から Terraform 入力を兼ねる（§2）。SDK 標準名の env が
既にあるので `MCML_TF_` を新設せず、そのまま `VAR_SPECS` に載せている。

## 2. GCP / Vertex AI（Phase 1）

ローカル対話作業は **ADC（`gcloud auth application-default login`）でキーレスを既定** とし、
SA キー JSON の発行は非対話自動化が必要になるまで行わない。

| キー名 | 用途 | 使用箇所 | 保管先 |
|---|---|---|---|
| （なし・ADC） | Terraform apply/destroy、Vertex SDK、`gcloud auth configure-docker` による Artifact Registry push | Terraform / `src/platforms/vertex` / docker push | gcloud ローカルセッション |
| `GOOGLE_CLOUD_PROJECT` | 実行プロジェクト ID（SDK 標準名）。**Terraform の `project_id` と state バケット名（`$GOOGLE_CLOUD_PROJECT-tfstate`）もこれが正本**（2026-08-02〜。それまでは `variables.tf` / `backend.tf` に直書きで、GCP だけ他4環境と非対称だった） | `VAR_SPECS` → Terraform / `src/platforms/vertex` | Doppler |
| `GOOGLE_CLOUD_REGION` | 実行リージョン（SDK 標準名） | 同上 | Doppler |
| `GOOGLE_APPLICATION_CREDENTIALS` | 非対話自動化が必要になった場合のみ発行する SA キー JSON のパス | （当面未使用・未発行） | Doppler（発行したら） |

- 非機密の**人が決める値**（`env/config.yaml`）: マシンサイズ / エンドポイント名 / データパス / イメージタグ
- **apply が決める値**（バケット名・SA email・イメージ URI）は `env/config.yaml` に書かない。
  `terraform output -json > artifacts/gcp-dev.outputs.json` を正とし、`src/platforms/shared/config.py` が読む
- Terraform 実行主体に必要なロール目安: `roles/aiplatform.admin`, `roles/storage.admin`,
  `roles/artifactregistry.admin`, `roles/iam.serviceAccountAdmin`, `roles/serviceusage.serviceUsageAdmin`
- **学習ジョブ実行用 SA は Terraform が作る「リソース」であり秘密ではない**（SA email を config で参照）。
  付与ロール目安: `roles/aiplatform.user`, 対象バケットの `roles/storage.objectAdmin`, `roles/artifactregistry.reader`
- cost 収集: 課金読み取りに `roles/billing.viewer`（＋ Billing export を使うなら BigQuery 読み取り）

## 3. AWS / SageMaker（Phase 2）

| キー名 | 用途 | 使用箇所 | 保管先 |
|---|---|---|---|
| `AWS_ACCESS_KEY_ID` | Terraform / boto3(SageMaker SDK) / ECR push / Cost Explorer 読み取りの実行主体 | Terraform, `src/platforms/sagemaker`, docker push, cost 収集 | Doppler |
| `AWS_SECRET_ACCESS_KEY` | 同上（ペア） | 同上 | Doppler |
| `AWS_DEFAULT_REGION` | 実行リージョン（`AWS_REGION` としても解決される） | 同上 | Doppler |
| `AWS_ACCOUNT_ID` | 非機密。ECR URI 等の組み立てに使用 | docker push | Doppler |

- **root ユーザ鍵をそのまま使っている**（`Arn ...:123456789012:root`）。個人ラボの規模に対して
  最小権限 IAM への差し替えは過剰と判断し、**意図的にこのまま**（2026-08-01・owner 判断）。
  影響は下の2点に限られる: **① SageMaker の投入側 permission friction が測れない**
  （実行ロール側は最小権限なので測れる）、**② AWS MCP を `--read-only` 固定にしている**（§MCP）。
- 非機密の**人が決める値**（`env/config.yaml`）: インスタンスタイプ / ジョブ名 prefix / エンドポイント名
- **apply が決める値**（S3 バケット名・ECR URL・Execution Role ARN）は `artifacts/aws-dev.outputs.json` を正とする
- **SageMaker Execution Role は Terraform が作るリソース**（ARN を config で参照、秘密ではない）。
  付与ポリシー目安: SageMaker 実行, 対象 S3 バケット読み書き, ECR pull, CloudWatch Logs 書き込み
- 実行主体側の権限目安: IAM ロール作成/削除, S3, ECR, SageMaker, CloudWatch Logs, `ce:GetCostAndUsage`
- 要件の核心である **「IAM 修正回数の計測」対象はこの Execution Role 側**。実行主体キーは最初から
  十分な権限を持たせ、計測ノイズにしない。

## 4. Azure / Azure ML（Phase 4・go/no-go 後）

Phase 2 完了の go 判定が出るまで **発行しない**（放置クレデンシャルを作らない）。

| キー名 | 用途 | 使用箇所 | 保管先 |
|---|---|---|---|
| `AZURE_SUBSCRIPTION_ID` | 対象サブスクリプション特定子（FreeTrial 枠） | Terraform, `src/platforms/azureml`, docker push, cost 収集 | Doppler（登録済み） |
| `AZURE_TENANT_ID` | Entra ID テナント特定子 | 同上 | Doppler（登録済み） |
| `AZURE_CLIENT_ID` | Service Principal。**非対話実行に必要。未発行** | 同上 | Doppler（発行したら） |
| `AZURE_CLIENT_SECRET` | 同 SP のシークレット。**未発行** | 同上 | Doppler（発行したら） |

- 現在の認証は `az login` のユーザ資格情報。上記 SP は go 判定後に発行する。

- SP ロール目安: 対象リソースグループの `Contributor` ＋ `AcrPush` ＋ `Cost Management Reader`。
  ロール割り当てを Terraform でやるなら追加で `User Access Administrator`（スコープは RG 限定）。
- 非機密の**人が決める値**（`env/config.yaml`）: インスタンスタイプ / デプロイ名 / 実験名
- **apply が決める値**（RG 名・ワークスペース名・ACR ログインサーバ・リージョン）は `artifacts/azure-dev.outputs.json` を正とする
- 留意: `check-residual` で Key Vault の soft-delete を消すには purge 権限が別途必要
  （[01_requirements.md](../01_requirements.md) §6 の残留リソース比較の対象）。

## 5. Snowflake（Phase 5）

> トライアルアカウント作成済み（2026-08-01・Standard / Asia Pacific Tokyo）。
> 実装は **Snowpark stored procedure（学習）＋ Model Registry（登録）＋ warehouse 推論**。
> SPCS は主経路にしない（要件で決定済み）ので image registry 系の資格情報は要らない。

| キー名 | 用途 | 読む主体 | 保管先 |
|---|---|---|---|
| `SNOWFLAKE_ORGANIZATION_NAME` | 組織名 | Terraform provider v2 | Doppler |
| `SNOWFLAKE_ACCOUNT_NAME` | アカウント名 | Terraform provider v2 | Doppler |
| `SNOWFLAKE_ACCOUNT` | `<org>-<account>`。**provider 用の2つとは形式が違う**ので別キーで持つ | Python connector | Doppler |
| `SNOWFLAKE_USER` | 実行ユーザー | 両方 | Doppler |
| `SNOWFLAKE_PRIVATE_KEY` | キーペア認証の RSA 秘密鍵（PKCS#8 PEM の本文） | 両方 | Doppler |
| `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE` | 鍵を暗号化した場合のみ | 両方 | Doppler |
| `SNOWFLAKE_ROLE` | `ACCOUNTADMIN`。**Terraform provider 専用** | Terraform provider | Doppler |
| `SNOWFLAKE_AUTHENTICATOR` | `SNOWFLAKE_JWT`。明示しないとパスワード認証に落ちる | Terraform provider | Doppler |

### ロールを2つに分ける（計測の前提）

`SNOWFLAKE_ROLE` は **Terraform 用**であって adapter が名乗るロールではない。

| 用途 | ロール | 解決経路 |
|---|---|---|
| Terraform apply / destroy | `ACCOUNTADMIN` | `SNOWFLAKE_ROLE`（Resource Monitor の作成に要る） |
| adapter（sproc / Model Registry / stage） | `MCML_DEV_ROLE` | terraform outputs の `role_name` → `SnowflakeConfig.role` |

adapter まで ACCOUNTADMIN で動かすと権限エラーが一度も起きず、**このラボ本命の
「最小権限で通るまでの試行回数」が Snowflake だけ常にゼロになる**。

### service user を切っていない（§0 の方針からの逸脱）

公開鍵を人間ユーザー（ACCOUNTADMIN）に付けた状態で運用している。30日のタイムボックスに対して
ユーザーを増やす見返りが小さいという判断で、権限分離はロール側で担保している
（[decision-log.md](../decisions/decision-log.md) 2026-08-01）。
人間ユーザーの MFA 必須化でキーペア接続が弾かれたら、`TYPE = SERVICE` のユーザーへ移す。
**回避策で粘らず、失敗した事実を `ml_runs` に残す。**

- 非機密（`env/config.yaml`）: role 名 / warehouse 名 / database / schema / stage 名。
  ただし **account identifier は Doppler 側**に置く（provider が env からしか読まないため。
  `AWS_ACCOUNT_ID` と同じ扱い）
- Neon 接続情報の受け渡し: 新規キーは不要。`NEON_MULTICLOUD_POOLED_URI` を
  `TF_VAR_neon_secret_string` として apply 時に渡し、Snowflake 側の secret に載せる
- 秘密鍵の置き場: Doppler が正本。**ローカルにも Snowflake の workspace にも残さない**
  （アカウントへの認証鍵をそのアカウントの中に置かない）
- cost 収集: `ACCOUNT_USAGE` ビューの参照権限を role に grant（追加の秘密は不要）
- `check-residual` 対象: warehouse（STARTED）/ スキーマ内オブジェクト / stage 成果物 /
  Fail-safe（7日・消せない固定行）

## 6. Databricks（Phase 3）

> **Free Edition のワークスペース作成済み**（2026-08-01）。ワークスペースは Databricks 側が
> 持つので、**土台クラウド（AWS）の資格情報は要らない**。
> `infra/environments/dbx-dev` の provider は workspace-level（`provider "databricks" {}`）で、
> 下の2件を env から読むだけ。

| キー名 | 用途 | 読む主体 | 保管先 |
|---|---|---|---|
| `DATABRICKS_HOST` | ワークスペース URL（`https://dbc-*.cloud.databricks.com`） | Terraform provider / databricks-sdk | Doppler |
| `DATABRICKS_TOKEN` | PAT（Settings → Developer → Access tokens） | 同上 | Doppler |

**発行済み（2026-08-01）**: 名前 `mcml-lab` / scope **all-apis** / lifetime 90日 =
**期限 2026-10-30**。この日付が Phase 3 の実行可能期間の上限で、切れると
apply も SDK も一斉に 401 になる（Snowflake のトライアル期限に相当する制約）。
疎通確認済み: `current_user.me()` → `<owner-email>`。

scope を `all-apis` にしたのは、**トークンの API scope は本ラボの測定対象ではない**ため。
比較軸の permission friction は Unity Catalog の grants（`databricks_grants`）で測る。
トークン側を絞ると基盤の権限モデルと無関係な失敗が `ml_runs` に混ざり、
Vertex の IAM 試行回数と並べたときに比較が濁る。

> 注: `cloud-ml-lab/dev` には別文脈の `DWH_DATABRICKS_TOKEN` が同居していたが、消費者が見つからないため
> 2026-08-01 に削除した（doppler-key名の再設計.md）。

### OAuth サービスプリンシパルを見送り、PAT を Doppler に置く（方針変更）

当初は `DATABRICKS_CLIENT_ID` / `DATABRICKS_CLIENT_SECRET`（OAuth M2M）を推奨し、PAT は
「手元検証のみ・`env/secret.yaml` 止まり」としていた。**Free Edition では SP の発行に要る
アカウントコンソール権限が無い見込み**のため PAT に一本化する。
`make` の各ターゲットは `doppler run --` 経由なので、保管先も `env/secret.yaml` ではなく Doppler。

- **PAT の有効期限を必ず記録する。** Snowflake のトライアル期限と同じく Phase 3 の
  実行可能期間を直接決める（databricks-phase-precheck.md）
- SP を作れると分かったら移行する。判断は
  [decision-log.md](../decisions/decision-log.md) 2026-08-01

### Doppler に入れないもの

- `TF_VAR_job_principal`（= ログインユーザーの email。grants の付与先。**空だと grants を
  1つも作らない**）/ `TF_VAR_create_catalog` / `TF_VAR_serving_model_version`
- catalog / schema / volume / job 名 → `env/config.yaml` と module 既定

### その他

- コンテナ: Model Serving は BYOC 非対応（サーバレス）。**BYOC 統一の前提が学習側しか
  成立しない**ことは比較の発見として記録する
- cost 収集: system tables（`system.billing.usage`）の参照権限（追加の秘密は不要。
  Free Edition で参照できるかは precheck 項目）
- `check-residual` 対象: serving endpoint, Unity Catalog のモデル版, Volume 上の成果物

## 7. 不要なもの（発行しない）

| 候補 | 不要な理由 |
|---|---|
| コンテナベースイメージの registry 認証 | 公開イメージのみ使用 |
| Terraform リモート state 用の認証 | backend は gcs / s3 / azurerm を使うが、いずれも**各クラウドの既存資格情報で読み書きする**（state 専用のキーは発行しない） |
| CI/CD 用トークン | CI なし。全 Phase ローカル実行 |
| 監視 / Feature Store / ドリフト検知系 | 要件で明示的に除外済み |

## 8. 運用ルール

- **発行は Phase 着手時、失効は Phase 完了時に判断する。** 特に Azure SP は go 判定前に作らない。
- 破綻条件「合計 ¥6,000/月 超過で Azure 切り離し」の実行手順には、SP 失効（secret 削除）を含める。
- 予算アラート（各クラウド ¥2,000/月）の設定は一度きりのコンソール/Terraform 作業であり、
  保管すべきクレデンシャルは発生しない。
- キーを追加・削除したら、このファイルと `doppler.yaml` のキー名コメントを同一変更で更新する。
- **`cloud-ml-lab/dev` は他プロジェクトと共有の config**（2026-07-31 時点で実在キー 59 件）。
  このファイルと `doppler.yaml` には **本ラボが参照する分だけ** を書く。共有 config の全件ミラーは
  作らない（他プロジェクトの増減で必ず腐るため、同日に全件ミラー方式を廃止した）。
- 実在キーとの突き合わせは `doppler secrets --project cloud-ml-lab --config dev --only-names` で行う。

## 9. MCP サーバの認証（2026-08-01 追加）

MCP サーバは**新しいクレデンシャルを増やさない**。既存の資格情報チェーンをそのまま使う。

**登録スコープ**: Claude Code をリポジトリ外（`/home/ubuntu/repos`）で起動する運用のため、5サーバとも**ユーザースコープ**に登録してある（プロジェクトスコープの `.mcp.json` は repo ルートで起動したときしか読まれない）。起動ラッパーの実体は `scripts/mcp/` にあり、ユーザースコープからは絶対パスで参照する。

| サーバ | 実体 | 認証の出どころ | 起動確認済みのツール数 |
|---|---|---|---|
| `terraform` | `hashicorp/terraform-mcp-server:1.1.0`（Docker） | **不要**（Registry 参照のみ） | 9 |
| `gcp` | `@google-cloud/gcloud-mcp`（googleapis 公式） | gcloud ADC（`gcloud auth application-default login`） | 1（`run_gcloud_command`） |
| `azure` | `@azure/mcp`（Microsoft 公式） | `az login` / 環境変数 | 7（group / storage / keyvault / acr / monitor / subscription） |
| `aws` | `mcp-proxy-for-aws` → AWS マネージド MCP（remote） | ローカルの AWS credential chain。無ければ `scripts/mcp/aws.sh` が Doppler 経由で解決する（資格情報ゼロだと remote 側が initialize を -32602 で拒否し接続失敗する・実測） | 6（`--read-only` 時）/ 9（外した時） |
| `neon` | `@neondatabase/mcp-server-neon` | Doppler `NEON_API_KEY`（`scripts/mcp/neon.sh`） | 既存 |

- **AWS は `--read-only` が既定**。実測（2026-08-01）で、この引数を付けると `aws___call_aws` / `aws___run_script` / `aws___get_presigned_url` がツール一覧から消え、ドキュメント参照だけになる。現在の資格情報が root アクセスキーのため（意図的にそのまま。§3 参照）、**この `--read-only` が唯一のガードレール**になっている。外さない。
- **GCP は denylist 付き**（`scripts/mcp/gcloud-policy.json`）: プロジェクト削除・課金操作・SA 鍵作成・Secret 読み出し・アクセストークン印字を拒否する。provider 側の既定 denylist は常に上乗せで効く。
- **Azure は `--read-only`**。3.0.0-beta.30 には **Azure ML 専用 namespace が無い**ため、Workspace 操作は `az ml` / SDK 側に残る。
- 認証が要るサーバは `doppler run -- claude` で Claude Code を起動しないとツール呼び出しが失敗する。
- MCP サーバは Claude Code の**起動時に読み込まれる**。登録を変えたらセッション（IDE ならウィンドウ）を再起動する。
- `claude.ai Gmail / Google Calendar / Google Drive` の `Needs Auth` は別系統（claude.ai のコネクタ設定で OAuth 認可が必要）。本ラボの5サーバとは無関係。
