# Vertex AI 用: API 有効化 / GCS / Artifact Registry / IAM / Endpoint（器）/ 予算アラート
#
# SDK に残る: Custom Job / Experiment / Model Upload / Deploy
#
# 境界の原則（docs/02_architecture.md「境界」）:
#   静的基盤 = Terraform / ジョブ実行・登録・デプロイ = SDK・CLI・SQL
#   terraform apply に学習実行を含めない。state に ML 実行履歴が混ざると
#   インフラ状態と実行履歴の両方の再現性が落ちる。
#
# 「Terraform でどこまで書けたか」自体が比較軸なので、
# 書けなかったもの・SDK に逃がしたものは docs/comparison/ に必ず残す。
#
# 移植元: ML/kaggle-bronze-gcp/infra/terraform/main.tf（Vertex E2E 実証済み）
#         + ML/gcp-search-mlops-gke/infra/terraform/{environments/dev/apis.tf,
#           modules/vertex/main.tf の google_vertex_ai_endpoint}
# 移植時の差分: BigQuery（dataset / experiments / cost_estimates）は移植しない。
#         計測到達点は Neon（docs/02_architecture.md「Neon 集約」）。

locals {
  name_prefix = "${var.project_name}-${var.environment}"

  # バケット名はグローバル一意なので project_id を含める
  bucket_name = var.gcs_bucket_name != "" ? var.gcs_bucket_name : "${var.project_id}-${local.name_prefix}"

  labels = {
    app         = "multicloud-ml-platform-lab"
    environment = var.environment
    platform    = "vertex"
  }

  # 有効化する API。学習実行に不要なものは足さない（有効化自体が比較材料）
  services = toset([
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "storage.googleapis.com",
    "iam.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "logging.googleapis.com",
    "cloudbilling.googleapis.com",
    "billingbudgets.googleapis.com",
  ])

  # 実行 SA のロール。用途別 SA 分離（gke 版）は採らない。
  # SA を増やすと permission friction の一次データが基盤差ではなく自作構成の差で汚れる。
  vertex_runner_roles = toset([
    "roles/aiplatform.user",
    "roles/artifactregistry.reader",
    "roles/storage.objectAdmin",
    "roles/logging.logWriter",
  ])
}

resource "google_project_service" "apis" {
  for_each = local.services

  project = var.project_id
  service = each.value

  # destroy で API を無効化しない（他リソースの巻き添え無効化を避ける）
  disable_on_destroy = false
}

# ----- ストレージ: 学習データ / 成果物 / JSONL fallback -----

resource "google_storage_bucket" "artifacts" {
  name                        = local.bucket_name
  project                     = var.project_id
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = var.bucket_force_destroy

  labels = local.labels

  depends_on = [google_project_service.apis]
}

# ----- コンテナ: 単一学習イメージ + 推論イメージ（Tier A 共通） -----

resource "google_artifact_registry_repository" "images" {
  project       = var.project_id
  location      = var.region
  repository_id = var.artifact_registry_repo
  format        = "DOCKER"
  description   = "multicloud-ml-platform-lab training / serving images"

  labels = local.labels

  depends_on = [google_project_service.apis]
}

# ----- IAM: Vertex 実行 SA（Custom Job / Endpoint が引き受ける） -----

resource "google_service_account" "vertex_runner" {
  project      = var.project_id
  account_id   = "${local.name_prefix}-vertex"
  display_name = "multicloud-ml-platform-lab Vertex runner (${var.environment})"
  description  = "Runs Vertex Custom Jobs and serves the deployed model."

  depends_on = [google_project_service.apis]
}

resource "google_project_iam_member" "vertex_runner" {
  for_each = local.vertex_runner_roles

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.vertex_runner.email}"
}

# ジョブ投入者が実行 SA を actAs するための binding。
# メールを持たない環境（CI / 別アカウント）では作らない。
resource "google_service_account_iam_member" "submitter_act_as" {
  count = var.vertex_submitter_email == "" ? 0 : 1

  service_account_id = google_service_account.vertex_runner.name
  role               = "roles/iam.serviceAccountUser"
  member             = "user:${var.vertex_submitter_email}"
}

# **SA 自身にも actAs を与える**（自己 impersonation）。
# Vertex AI Pipelines のステップはこの SA として走り、その中の run_phase が
# CustomJob を「同じ SA で」投入する。人間ユーザーの binding だけでは
# `You do not have permission to act as service_account` で落ちる
# （2026-08-02 にパイプライン実投入で実測）。
#
# CLI 実行（人間の ADC → 投入）では踏まない経路なので、**パイプライン化して
# 初めて必要になった権限**。ここが Vertex の step=コンテナ実行という制約の帰結。
resource "google_service_account_iam_member" "runner_act_as_self" {
  service_account_id = google_service_account.vertex_runner.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.vertex_runner.email}"
}

# ----- Endpoint（器のみ） -----
#
# モデルの deploy（deployed_models / traffic_split）は provider の computed 扱いで、
# SDK 側（src/platforms/vertex/adapter.py の deploy()）が server-side に書き換える。
# ここで管理すると SDK deploy と競合するため managed resource に含めない。
#
# 撤退時の注意: deploy 済みモデルが残ったまま destroy すると HTTP 400 で落ちる。
# adapter.teardown()（undeploy_all → delete(force)）を先に回すこと。

resource "google_vertex_ai_endpoint" "main" {
  count = var.enable_endpoint_shell ? 1 : 0

  project      = var.project_id
  location     = var.region
  name         = "${local.name_prefix}-endpoint"
  display_name = "${local.name_prefix}-endpoint"
  description  = "California Housing regressor endpoint (Tier A / Vertex AI)"

  labels = local.labels

  depends_on = [google_project_service.apis]
}

# ----- 予算アラート -----
#
# Tier A は各 ¥2,000/月（docs/01_requirements.md）。合計 ¥8,000/月 超過で Azure を切り離す。
#
# budget_filter.projects は project **番号** を要求する（ID ではない）ため data source で引く。
# 予算を作らない設定では API を叩かないよう count を揃える。

data "google_project" "current" {
  count = var.billing_account_id == "" ? 0 : 1

  project_id = var.project_id
}

resource "google_billing_budget" "monthly_guardrail" {
  count = var.billing_account_id == "" ? 0 : 1

  billing_account = var.billing_account_id
  display_name    = "${local.name_prefix} monthly guardrail"

  budget_filter {
    projects        = ["projects/${data.google_project.current[0].number}"]
    calendar_period = "MONTH"
  }

  amount {
    specified_amount {
      currency_code = "JPY"
      units         = tostring(var.budget_amount_jpy)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }

  threshold_rules {
    threshold_percent = 0.9
  }

  threshold_rules {
    threshold_percent = 1.0
  }

  depends_on = [google_project_service.apis]
}
