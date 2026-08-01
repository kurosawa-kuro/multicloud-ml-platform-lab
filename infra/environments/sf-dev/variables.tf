# default に置いてよいのは非秘密の識別子だけ。
# アカウント識別子・ユーザー・パスワードは provider が環境変数（Doppler）から読む。
# Neon の資格情報は TF_VAR_neon_secret_string で渡す。

variable "project_name" {
  type    = string
  default = "mcml"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "warehouse_size" {
  description = "X-Small で足りる"
  type        = string
  default     = "XSMALL"
}

variable "create_resource_monitor" {
  description = "ACCOUNTADMIN が無いトライアルでは false（auto_suspend は残る）"
  type        = bool
  default     = true
}

variable "credit_quota" {
  description = "月次クレジット上限。トライアルのクレジットを一気に溶かさないための保険"
  type        = number
  default     = 10
}

variable "grant_to_user" {
  description = "TF_VAR_grant_to_user で渡す。空ならロールを誰にも付与しない"
  type        = string
  default     = ""
}

variable "neon_host" {
  description = "TF_VAR_neon_host で渡す。空なら network rule / secret / EAI を作らない"
  type        = string
  default     = ""
}

variable "create_neon_secret" {
  description = "Neon 資格情報の secret を作るか（値は TF_VAR_neon_secret_string）"
  type        = bool
  default     = false
}

variable "neon_secret_string" {
  description = "TF_VAR_neon_secret_string で渡す Neon 接続文字列"
  type        = string
  default     = ""
  sensitive   = true
}
