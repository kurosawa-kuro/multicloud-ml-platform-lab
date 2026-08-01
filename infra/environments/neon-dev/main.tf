# neon-dev : modules/neon の dev 環境インスタンス。
#
# フェーズ末に必ず destroy する（Tier A のマネージドエンドポイントは常時課金）。
# destroy 後の残留は scripts/check_residual.py で列挙し infra_events へ記録する。

module "neon" {
  source = "../../modules/neon"

  project_name = var.project_name
  environment  = var.environment
}
