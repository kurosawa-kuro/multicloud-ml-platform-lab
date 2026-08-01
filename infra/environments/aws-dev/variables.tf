# default に置いてよいのは非秘密の識別子だけ。
# 通知先 email は "" 既定にし、TF_VAR_ 環境変数（Doppler）で渡す。
# AWS アカウント ID はコードに書かず data.aws_caller_identity で解決する。

variable "project_name" {
  type    = string
  default = "mcml"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "region" {
  description = "ap-northeast-1（Doppler の AWS_DEFAULT_REGION と一致）"
  type        = string
  default     = "ap-northeast-1"
}

# ----- 2 段階 apply 用（学習後に -var で渡す） -----

variable "model_data_url" {
  description = "学習成果物 model.tar.gz の S3 URI。空なら Model / Endpoint Config / Endpoint を作らない"
  type        = string
  default     = ""
}

variable "serving_image_uri" {
  description = "推論コンテナの ECR イメージ URI（タグ込み）。空なら Model 以降を作らない"
  type        = string
  default     = ""
}

variable "endpoint_instance_type" {
  description = "リアルタイム推論のインスタンスタイプ。常時課金なのでフェーズ末に必ず destroy する"
  type        = string
  default     = "ml.t2.medium"
}

variable "budget_amount_usd" {
  description = "Tier A の月次予算上限（docs/01_requirements.md: 各 ¥2,000/月 相当）"
  type        = number
  default     = 15
}

variable "budget_notification_email" {
  description = "TF_VAR_budget_notification_email で渡す。空なら予算アラートを作らない"
  type        = string
  default     = ""
}
