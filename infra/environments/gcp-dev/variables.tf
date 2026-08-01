# default に置いてよいのは非秘密の識別子だけ。
# 請求先アカウント ID と個人メールは "" 既定にし、TF_VAR_ 環境変数（Doppler）で渡す。

variable "project_name" {
  type    = string
  default = "mcml"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "project_id" {
  # default を置かない。実プロジェクト ID を repo に焼くと、公開時にそのまま出る上、
  # 「Doppler が正本」という他4環境との対称性も崩れる（GCP だけ TF 直書きだった）。
  # 値は Doppler の GOOGLE_CLOUD_PROJECT から `scripts/run_terraform.py` が渡す
  # （src/platforms/shared/terraform_vars.py の VAR_SPECS）。未解決なら terraform を
  # 起動する前に名前を挙げて落ちる。
  description = "GCP プロジェクト ID（Doppler: GOOGLE_CLOUD_PROJECT）"
  type        = string
}

variable "region" {
  description = "us-central1（既存 GCP 資産と同一リージョン。ロケーション差はコスト比較のノイズになる）"
  type        = string
  default     = "us-central1"
}

variable "enable_endpoint_shell" {
  description = "Vertex AI Endpoint の器を Terraform で作るか"
  type        = bool
  default     = true
}

variable "vertex_submitter_email" {
  description = "ジョブ投入者。TF_VAR_vertex_submitter_email で渡す。空なら actAs binding を作らない"
  type        = string
  default     = ""
}

variable "billing_account_id" {
  description = "TF_VAR_billing_account_id で渡す。空なら予算アラートを作らない"
  type        = string
  default     = ""
}

variable "budget_amount_jpy" {
  description = "Tier A の月次予算上限（docs/01_requirements.md: 各 ¥2,000/月）"
  type        = number
  default     = 2000
}
