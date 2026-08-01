# default に置いてよいのは非秘密の識別子だけ。
# workspace host / token は provider が環境変数（Doppler）から読む。
# principal は "" 既定にし、TF_VAR_job_principal で渡す。

variable "project_name" {
  type    = string
  default = "mcml"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "create_catalog" {
  description = "メタストア未割り当てのトライアル環境では false にして既存カタログを使う"
  type        = bool
  default     = true
}

variable "catalog_name" {
  description = "空なら mcml_dev を導出する"
  type        = string
  default     = ""
}

variable "schema_name" {
  description = <<-EOT
    スキーマ名。既存カタログに相乗りする場合（create_catalog=false）は
    **ここにラボ接頭辞を入れる**。カタログ名で絞れなくなるため、
    残留検査の LAB_NAME_PREFIX がスキーマ名にしか掛からない。
  EOT
  type        = string
  default     = "ml"
}

variable "job_principal" {
  description = "TF_VAR_job_principal で渡す（email / グループ名 / サービスプリンシパル app ID）。空なら grants を作らない"
  type        = string
  default     = ""
}

variable "wheel_path" {
  description = "空なら Volume 配下の既定パスを使う。make のアップロード先と一致させる"
  type        = string
  default     = ""
}

variable "serving_model_version" {
  description = "配信するモデルバージョン。学習・登録後に -var で渡す。空ならエンドポイントを作らない"
  type        = string
  default     = ""
}
