# Vertex AI 用: API 有効化 / GCS / Artifact Registry / IAM / Endpoint（器）/ 予算アラート

variable "project_name" {
  description = "リソース名の接頭辞。命名は locals で一元化する"
  type        = string
}

variable "environment" {
  description = "dev / stg / prd"
  type        = string
  default     = "dev"
}

variable "project_id" {
  description = "GCP プロジェクト ID"
  type        = string
}

variable "region" {
  description = "GCS / Artifact Registry / Vertex AI のロケーション"
  type        = string
  default     = "us-central1"
}

variable "gcs_bucket_name" {
  description = "学習データ・成果物・JSONL fallback を置くバケット名。空なら locals で導出する（バケット名はグローバル一意）"
  type        = string
  default     = ""
}

variable "artifact_registry_repo" {
  description = "学習・推論コンテナを置く Docker リポジトリ ID"
  type        = string
  default     = "mcml"
}

variable "bucket_force_destroy" {
  description = "ラボ既定 true。Tier A はフェーズ末に必ず destroy する設計（docs/02_architecture.md「境界」）。本番相当で使うなら false"
  type        = bool
  default     = true
}

variable "enable_endpoint_shell" {
  description = "Vertex AI Endpoint の器を Terraform で作るか。モデルの deploy 自体は SDK 側の責務"
  type        = bool
  default     = true
}

variable "vertex_submitter_email" {
  description = "実行 SA を actAs できる人間ユーザー。空なら binding を作らない（リポジトリに実メールを直書きしない）"
  type        = string
  default     = ""
}

variable "billing_account_id" {
  description = "予算アラートを有効にする請求先アカウント ID。**必須**（空だと google_billing_budget が作られない）"
  type        = string
  default     = ""

  # **空を許さない。** 2026-08-01 まで「空なら黙って作らない」だったため、
  # Azure では spendingLimit=Off と重なり**ガードレールが1つも無い状態で
  # 常時課金エンドポイントを立てた**時間帯が実在した。値は Doppler から
  # run_terraform.py が渡す（env/config.yaml に実メールを直書きしない）。
  validation {
    condition     = length(trimspace(var.billing_account_id)) > 0
    error_message = "請求先アカウント ID が空。Doppler の MCML_TF_BILLING_ACCOUNT_ID を設定する（ガードレール無しで apply しない）。"
  }
}

variable "budget_amount_jpy" {
  description = "月次予算（円）。Tier A の上限は各 ¥2,000/月（docs/01_requirements.md）"
  type        = number
  default     = 2000
}
