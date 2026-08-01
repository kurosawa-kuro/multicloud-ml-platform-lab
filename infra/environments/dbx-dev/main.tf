# dbx-dev : modules/databricks の dev 環境インスタンス。
#
# フェーズ末に必ず destroy する（Tier A のマネージドエンドポイントは常時課金）。
# destroy 後の残留は scripts/check_residual.py で列挙し infra_events へ記録する。
# Tier B の残留候補は質が違う（UC 管理テーブル/ボリュームのデータ・Serving Endpoint 定義）。
#
# 2 段階 apply:
#   1) 学習前 — 変数なしで apply（Catalog / Schema / Volume / Grants / Model / Job / Policy）
#   2) 版登録後 — serving_model_version を渡して apply（Serving Endpoint）

module "databricks" {
  source = "../../modules/databricks"

  project_name = var.project_name
  environment  = var.environment

  create_catalog = var.create_catalog
  catalog_name   = var.catalog_name
  schema_name    = var.schema_name
  job_principal  = var.job_principal
  wheel_path     = var.wheel_path

  serving_model_version = var.serving_model_version
}
