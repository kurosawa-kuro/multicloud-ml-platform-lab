# Snowflake 用: Database / Schema / Warehouse / Role / Grants / Stage / Network Rule / Secret / External Access Integration
#
# adapter（src/platforms/snowflake/）と check_residual.py が参照する ID を出す。
# 出力していないリソースは残留検査から漏れる。

output "database_name" {
  description = "データベース名"
  value       = snowflake_database.main.name
}

output "schema_name" {
  description = "スキーマ名（sproc / モデル / ステージの置き場）"
  value       = snowflake_schema.main.name
}

output "warehouse_name" {
  description = "実行 warehouse（auto_suspend 済み）"
  value       = snowflake_warehouse.main.name
}

output "role_name" {
  description = "実行ロール"
  value       = snowflake_account_role.main.name
}

output "stage_name" {
  description = "パッケージのアップロード先ステージ名"
  value       = snowflake_stage_internal.code.name
}

output "stage_path" {
  description = "PUT / import で使う修飾名（@DB.SCHEMA.STAGE）"
  value       = "@${snowflake_database.main.name}.${snowflake_schema.main.name}.${snowflake_stage_internal.code.name}"
}

output "resource_monitor_name" {
  description = "クレジット上限。作らない設定なら null"
  value       = var.create_resource_monitor ? snowflake_resource_monitor.main[0].name : null
}

output "network_rule_name" {
  description = "Neon への EGRESS ルール。neon_host 未設定なら null"
  value       = local.external_access_enabled ? snowflake_network_rule.neon_egress[0].name : null
}

output "secret_name" {
  description = "Neon 接続情報の secret 名。値は出さない"
  value       = local.secret_enabled ? snowflake_secret_with_generic_string.neon[0].name : null
}

output "external_access_integration_name" {
  description = "sproc の EXTERNAL_ACCESS_INTEGRATIONS に渡す名前。Terraform ネイティブ資源が無く snowflake_execute で作っている"
  value       = local.eai_enabled ? local.eai_name : null
}

output "time_travel_retention_days" {
  description = "残留比較用。Fail-safe（7日）はこの値と無関係に残る"
  value       = var.data_retention_time_in_days
}
