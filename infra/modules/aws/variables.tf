# SageMaker AI 用: S3 / ECR / IAM Role / Model Package Group / Endpoint Config / Endpoint

variable "project_name" {
  description = "リソース名の接頭辞。命名は locals で一元化する"
  type        = string
}

variable "environment" {
  description = "dev / stg / prd"
  type        = string
  default     = "dev"
}

variable "region" {
  description = "AWS リージョン（Doppler の AWS_DEFAULT_REGION と一致させる）"
  type        = string
  default     = "ap-northeast-1"
}

variable "bucket_name" {
  description = "学習データ・成果物・JSONL fallback を置くバケット名。空なら locals で導出する（バケット名はグローバル一意）"
  type        = string
  default     = ""
}

variable "bucket_force_destroy" {
  description = "ラボ既定 true。Tier A はフェーズ末に必ず destroy する設計（docs/02_architecture.md「境界」）。本番相当で使うなら false"
  type        = bool
  default     = true
}

variable "ecr_repositories" {
  description = "作成する ECR リポジトリ。ECR は 1 repo = 1 イメージなので training / serving を分ける（Artifact Registry との構造差）"
  type = map(object({
    image_tag_mutability = string
    scan_on_push         = bool
    max_image_count      = number
  }))
  default = {
    training = {
      image_tag_mutability = "MUTABLE"
      scan_on_push         = true
      max_image_count      = 10
    }
    serving = {
      image_tag_mutability = "MUTABLE"
      scan_on_push         = true
      max_image_count      = 10
    }
  }
}

variable "ecr_force_delete" {
  description = "イメージが残っていてもリポジトリを削除する。ラボ既定 true（destroy が image 残存で失敗するのを避ける）"
  type        = bool
  default     = true
}

# ----- 2 段階 apply 用（学習後に埋める） -----

variable "model_data_url" {
  description = "学習成果物 model.tar.gz の S3 URI。空なら Model / Endpoint Config / Endpoint を作らない（学習前でも基盤だけ apply できる）"
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

variable "endpoint_instance_count" {
  description = "推論インスタンス数。比較は 1 台で足りる"
  type        = number
  default     = 1
}

# ----- 予算アラート -----

variable "budget_amount_usd" {
  description = "月次予算（USD）。Tier A の上限は各 ¥2,000/月（docs/01_requirements.md）。AWS Budgets は JPY 指定不可"
  type        = number
  default     = 15
}

variable "budget_notification_email" {
  description = "予算アラートの通知先。**必須**（空だと aws_budgets_budget が作られない）"
  type        = string
  default     = ""

  # **空を許さない。** 2026-08-01 まで「空なら黙って作らない」だったため、
  # Azure では spendingLimit=Off と重なり**ガードレールが1つも無い状態で
  # 常時課金エンドポイントを立てた**時間帯が実在した。値は Doppler から
  # run_terraform.py が渡す（env/config.yaml に実メールを直書きしない）。
  validation {
    condition     = length(trimspace(var.budget_notification_email)) > 0
    error_message = "予算アラートの通知先が空。Doppler の MCML_TF_BUDGET_EMAIL を設定する（ガードレール無しで apply しない）。"
  }
}
