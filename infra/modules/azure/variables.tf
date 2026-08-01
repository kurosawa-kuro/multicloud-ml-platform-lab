# Azure ML 用: Resource Group / Storage / ACR / Key Vault / App Insights / Workspace / Compute Cluster

variable "project_name" {
  description = "リソース名の接頭辞。命名は locals で一元化する"
  type        = string
}

variable "environment" {
  description = "dev / stg / prd"
  type        = string
  default     = "dev"
}

variable "location" {
  description = "Azure リージョン"
  type        = string
  default     = "japaneast"
}

variable "resource_group_name" {
  description = "リソースグループ名。空なら locals で導出する"
  type        = string
  default     = ""
}

# ----- Storage / Key Vault / App Insights -----

variable "storage_replication_type" {
  description = "ラボは LRS で十分（GRS はコストが倍）"
  type        = string
  default     = "LRS"
}

variable "key_vault_soft_delete_retention_days" {
  description = "最小値 7。長くすると destroy 後の論理削除が長引き、同名再作成が詰まる"
  type        = number
  default     = 7
}

# ----- ACR -----

variable "create_container_registry" {
  description = "ACR を作るか。Workspace 自体には必須ではないが BYOC（Tier A 統一単位）には要る"
  type        = bool
  default     = true
}

variable "acr_sku" {
  description = "Basic で足りる（Premium は Private Link 用）"
  type        = string
  default     = "Basic"
}

# ----- Workspace / Compute Cluster -----

variable "public_network_access_enabled" {
  description = "Private Link なしの最小シナリオ（docs/tasks の Non-scope）。true 固定運用"
  type        = bool
  default     = true
}

variable "compute_cluster_vm_size" {
  description = "学習ノードの VM サイズ。CPU のみ（LightGBM）"
  type        = string
  default     = "Standard_DS3_v2"
}

variable "compute_cluster_min_nodes" {
  description = "0 固定。1 以上にするとジョブが無くても課金される"
  type        = number
  default     = 0
}

variable "compute_cluster_max_nodes" {
  description = "比較は 1 台で足りる"
  type        = number
  default     = 1
}

variable "compute_cluster_idle_duration" {
  description = "アイドル後にノードを落とすまでの時間（ISO8601 duration）"
  type        = string
  default     = "PT5M"
}

variable "compute_cluster_vm_priority" {
  description = "LowPriority（Spot 相当）でコストを下げる。停止耐性は学習ジョブ側で吸収する"
  type        = string
  default     = "LowPriority"
}

# ----- 予算アラート -----

variable "budget_amount" {
  description = "月次予算。通貨はサブスクリプションの請求通貨（JPY 想定）。Tier A は各 ¥2,000/月（docs/01_requirements.md）"
  type        = number
  default     = 2000
}

variable "budget_start_date" {
  description = "予算期間の開始日（月初・UTC・ISO8601）。Terraform 内で現在時刻を作らないため変数で外出しする"
  type        = string
  default     = "2026-08-01T00:00:00Z"
}

variable "budget_notification_email" {
  description = "予算アラートの通知先。**必須**（空だと予算が作られない）"
  type        = string
  default     = ""

  # **空を許さない。** 2026-08-01 まで「空なら黙って作らない」だったため、
  # Azure では spendingLimit=Off と重なり**ガードレールが1つも無い状態で
  # 常時課金エンドポイントを立てた**時間帯が実在した。値は Doppler から
  # run_terraform.py が渡す（env/config.yaml に実メールを直書きしない）。
  validation {
    condition     = length(trimspace(var.budget_notification_email)) > 0
    error_message = "予算アラートの通知先が空。Doppler の MCML_TF_BUDGET_EMAIL を設定する（spendingLimit=Off のため唯一のガードレール）。"
  }
}
