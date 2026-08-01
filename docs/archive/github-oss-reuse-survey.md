# GitHub 流用可能リポジトリ調査（生ブレスト退避）

> **退避元**: チャット貼り付け（2026-07-31 に distill-spec で退避）
> **蒸留先**: reuse-asset-import-map.md「移植の記録」/ precheck タスク / `01_requirements.md` 非対象
> **権威**: この文書は archive であり権威順位は最下位。リポジトリの実在・ライセンスは未検証（着手時に確認）。

---

GitHubで流用可能なリポジトリを探します。4基盤とも一次リポジトリが存在するので、自作する部分はほとんどありません。探した結果、設計に影響する発見が3つありました。

## 発見1: 機能比較マトリクスは既に公開されている

thoughtworks/mlops-platforms が SageMaker・VertexAI・AzureML・Databricks などの比較マトリクスを、ベンダードキュメントへのリンク付きのオープン形式で公開しています。

つまり **v2で捨てた「機能比較表」は、作っても既存の劣化コピーにしかなりません**。逆に言えば、実測値（IAM試行回数・残留リソース・Neon到達経路）へ振ったv2の判断は正しかったことになります。この repo は**埋めるべき空白を確認するための参照先**として使い、成果物として競合させない。

## 発見2: Snowflake公式サンプルは別のCalifornia Housing

これが最大の罠です。

Snowflake-Labs/sfguide-snowpark-scikit-learn は、Imputation・Scaling・One-Hot Encoding を含む scikit-learn パイプラインと RandomForestRegressor でカリフォルニアの住宅価格中央値を予測し、Snowpark Python ストアドプロシージャで学習してステージへ保存します。構成は理想的に近いのですが、扱っている列は HOUSING_MEDIAN_AGE / TOTAL_ROOMS / TOTAL_BEDROOMS / HOUSEHOLDS / OCEAN_PROXIMITY です。

これは **Kaggle版（handson-ml版）** であって、`sklearn.datasets.fetch_california_housing` とは列も目的変数のスケールも違います。One-Hot Encoding が入っているのは `OCEAN_PROXIMITY` があるからで、sklearn版には存在しません。

**配管（sproc登録・stage・Model Registry・UDF推論）だけ流用し、データ層は必ず差し替える。** ここを見落とすと Snowflake だけ RMSE が合わず、原因究明に時間を溶かします。

## 発見3: SageMaker の BYOC は既製品がある

aws-samples/amazon-sagemaker-ml-pipeline-deploy-with-terraform の `sagemaker_byo` フォルダが、aws/amazon-sagemaker-examples の scikit_bring_your_own を土台にした例になっています。v2で決めた BYOC 統一方針に、そのまま乗る構成です。

---

## 基盤別 流用候補

### Azure ML

| リポジトリ | 取るもの |
|---|---|
| `microsoft/azureml-terraform-examples` | 本命。Workspace・Storage Account・Key Vault・Container Registry を、Private Link や VNET binding の有無でシナリオ別に構成した例。**Private Link なしの最小シナリオだけ抜く** |
| `Azure/terraform-azurerm-avm-res-machinelearningservices-workspace` | Azure Verified Module。`kind` 変数で標準Workspace / AI Hub / Project を切り替える設計。モジュール変数の切り方の参考 |
| `Azure/azureml-examples` | CLI v2 例・MLflow トラッキングのnotebook・MITライセンス。Command Job のYAML定義を取る |
| `csiebler/azure-machine-learning-terraform` | `azurerm_machine_learning_workspace` が app_insights / key_vault / storage / ACR の4つのIDを受け取る構造が一目で読める。**依存の実数を見積もる用**（Private Link部分は捨てる） |

Phase 4 の見積りに必要だった「App Insights と ACR が必須か」は、この4つ目のファイルで確認できます。

### SageMaker

| リポジトリ | 取るもの |
|---|---|
| `aws-samples/amazon-sagemaker-ml-pipeline-deploy-with-terraform` | **本命**。ECR + BYOC + Terraform の最小構成 |
| `aws/amazon-sagemaker-examples` の `advanced_functionality/scikit_bring_your_own` | BYOCコンテナの `/opt/ml` 契約実装そのもの。`entrypoint_sagemaker.sh` の元ネタ |
| `aws-samples/aws-mlops-pipelines-terraform` | Notebooks フォルダに、Pipelineを使う版と SageMaker SDK 単体版の2つのnotebookがある。**SDK単体版だけ**参照 |

### Databricks

| リポジトリ | 取るもの |
|---|---|
| `databricks/terraform-databricks-examples` | 公式。modules / examples / cicd-pipelines の3層構成で、Azure・AWS・GCP それぞれのワークスペース展開例 |
| `databricks/mlops-stacks` | 権限の一次情報。UC配下にモデルを登録するには USE_CATALOG・USE_SCHEMA・MODIFY・CREATE_MODEL・CREATE_TABLE が必要で、`staging.<schema>.<model>` のように環境ごとにカタログが変わる。**ただし Terraform ではなく Asset Bundles ベース**なので、構成は移植せず権限リストだけ取る |
| `terraform-provider-databricks` の `docs/resources/job.md`, `model_serving.md` | serverless上で `python_wheel_task` を動かすには `environment_key` が必要、カスタムモデル配信は `entity_name`・`entity_version`・`workload_size`・`scale_to_zero_enabled` の組で指定する。wheel配布とscale-to-zeroの実装根拠 |
| `data-platform-hq/terraform-databricks-unity-catalog` | catalog / schema / grants をリスト定義でまとめる書き方 |

### Snowflake

| リポジトリ | 取るもの |
|---|---|
| `IndexSeek/snowflake-ml-example-project` | **構成として本命**。notebookではなくPythonモジュール構成で、外部オーケストレータから実行する方式と、ストアドプロシージャ＋タスクとしてSnowflake内で完結させる方式の両方を持つ。前者が Tier B の実装形に一致 |
| `Snowflake-Labs/sfguide-snowpark-scikit-learn` | 配管のみ。`sproc()` 呼び出し時に packages でSnowflake Anaconda channel にあるライブラリを明示する形式が、依存宣言の書き方の実例 |
| `snowflakedb/snowflake-ml-python` | Modeling APIは sklearn / xgboost / lightgbm ベースのクラス群を持ち、MLOps側は Registry を含む。Model Registry はスキーマレベルのオブジェクトとしてモデルを保存し、warehouse上での推論、または SPCS でのサービス化ができる |
| `tekumara/snowflake-terraform-kit` / `getindata/terraform-snowflake-role` | role/grant のモジュール化。grantリソースは (resource\|schema\|object\|priv\|grantoption) の組ごとに1つしか作れず、複数作ると相互に上書きし合うという落とし穴の回避策付き |

---

## 避けるべきもの

```text
aws-samples/mlops-terraform-template   : Studio + Projects + VPC + KMS 前提。重すぎる
aws-samples/sagemaker-mlops-with-terraform : GitLab依存。CI/CDは今回のスコープ外
Azure の Private Link / VNET 付きシナリオ : コストが予算枠を壊す
Snowflake の Feature Store デモ         : v2で明示除外した領域
databricks/mlops-stacks の全体構成       : Terraform比較という目的と手段が食い違う
```

共通して、**CI/CD統合を含むテンプレートは全部外す**。v2の完了条件（apply → 学習 → 登録 → 1件推論 → destroy → 残留確認）に CI/CD は入っていません。

---

## 着手前に確認が要る2点

Snowflake の Terraform プロバイダは、source名がリネームされており、versions.tf の更新が必要で、さらにプレビュー機能は既定で無効、`preview_features_enabled` に機能名を明示的に追加する必要があり、プレビュー機能はメジャーバージョンが変わらなくても破壊的変更が入りうるとされています。External Access Integration 周辺がこの扱いに該当するかは、着手時点の `MIGRATION_GUIDE.md` で確認してください。

ライセンスは、ベンダー公式配下（microsoft / Azure / aws-samples / databricks / Snowflake-Labs）は寛容ライセンスが通例ですが、個人リポジトリを含めて clone 時に LICENSE を必ず確認し、流用元を `docs/comparison/` に記録してください。**どこから何を借りたかの記録自体が、レポートの再現性の一部**になります。
