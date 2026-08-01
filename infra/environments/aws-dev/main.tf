# aws-dev : modules/aws の dev 環境インスタンス。
#
# フェーズ末に必ず destroy する（Tier A のマネージドエンドポイントは常時課金）。
# destroy 後の残留は scripts/check_residual.py で列挙し infra_events へ記録する。
#
# 2 段階 apply:
#   1) 学習前 — 変数なしで apply（S3 / ECR / IAM / Model Package Group まで）
#   2) 学習後 — model_data_url と serving_image_uri を渡して apply（Model 以降）

module "aws" {
  source = "../../modules/aws"

  project_name = var.project_name
  environment  = var.environment

  region = var.region

  model_data_url         = var.model_data_url
  serving_image_uri      = var.serving_image_uri
  endpoint_instance_type = var.endpoint_instance_type

  budget_amount_usd         = var.budget_amount_usd
  budget_notification_email = var.budget_notification_email
}
