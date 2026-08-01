# default に置いてよいのは非秘密の識別子だけ。
# subscription ID・通知先 email は "" 既定にし、TF_VAR_ 環境変数（Doppler）で渡す。
# テナント ID は data.azurerm_client_config で解決する。

variable "project_name" {
  type    = string
  default = "mcml"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "location" {
  description = "japaneast（他2基盤は us-central1 / ap-northeast-1。ロケーション差はコスト比較の注記に残す）"
  type        = string
  default     = "japaneast"
}

variable "subscription_id" {
  description = "空なら ARM_SUBSCRIPTION_ID 環境変数（Doppler）を使う"
  type        = string
  default     = ""
}

variable "compute_cluster_vm_size" {
  description = "学習ノードの VM サイズ（CPU のみ）"
  type        = string
  default     = "Standard_DS3_v2"
}

variable "compute_cluster_vm_priority" {
  description = <<-EOT
    LowPriority（Spot 相当）。Vertex の Spot と実行形態を揃えるためこちらを既定にする。
    2026-08-01 の経緯: 当初 TotalLowPriorityCores が 0/0 で、LowPriority クラスタは
    ClusterMinNodesExceedCoreQuota（total vCPU quota of 0）で作成に失敗した。
    プラン変更後に quota 申請（TotalLowPriorityCores -> 8）が通り、実需 4 vCPU を賄えるようになった。
    枠が足りない環境では "Dedicated" へ落とす（TotalDedicatedCores 0/20・family 0/6 で足りる）。
    判定手順は docs/runbooks/動作検証-azureml.md §1。
  EOT
  type        = string
  default     = "LowPriority"
}

variable "budget_amount" {
  description = "Tier A の月次予算上限（docs/01_requirements.md: 各 ¥2,000/月）。通貨は請求通貨"
  type        = number
  default     = 2000
}

variable "budget_start_date" {
  description = "予算期間の開始日（月初・UTC）。Phase 4 着手時に更新する"
  type        = string
  default     = "2026-08-01T00:00:00Z"
}

variable "budget_notification_email" {
  description = "TF_VAR_budget_notification_email で渡す。空なら予算アラートを作らない"
  type        = string
  default     = ""
}
