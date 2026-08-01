# gcp-dev : modules/gcp の dev 環境インスタンス。
#
# フェーズ末に必ず destroy する（Tier A のマネージドエンドポイントは常時課金）。
# destroy 後の残留は scripts/check_residual.py で列挙し infra_events へ記録する。

module "gcp" {
  source = "../../modules/gcp"

  project_name = var.project_name
  environment  = var.environment

  project_id = var.project_id
  region     = var.region

  enable_endpoint_shell  = var.enable_endpoint_shell
  vertex_submitter_email = var.vertex_submitter_email

  billing_account_id = var.billing_account_id
  budget_amount_jpy  = var.budget_amount_jpy
}
