# sf-dev : modules/snowflake の dev 環境インスタンス。
#
# フェーズ末に必ず destroy する（Tier A のマネージドエンドポイントは常時課金）。
# destroy 後の残留は scripts/check_residual.py で列挙し infra_events へ記録する。
# Snowflake 固有の残留候補: Time Travel / Fail-safe / ステージ成果物
# （Fail-safe の 7 日は設定で消せない = Tier A に無い種類の残留）。

module "snowflake" {
  source = "../../modules/snowflake"

  project_name = var.project_name
  environment  = var.environment

  warehouse_size          = var.warehouse_size
  create_resource_monitor = var.create_resource_monitor
  credit_quota            = var.credit_quota

  grant_to_user = var.grant_to_user

  neon_host          = var.neon_host
  create_neon_secret = var.create_neon_secret
  neon_secret_string = var.neon_secret_string
}
