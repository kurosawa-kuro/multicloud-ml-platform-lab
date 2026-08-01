# apply 直後に adapter と check_residual.py へ渡す値をここへ集約する。
#
#   terraform -chdir=infra/environments/sf-dev output -json > artifacts/sf-dev.outputs.json

output "database_name" {
  value = module.snowflake.database_name
}

output "schema_name" {
  value = module.snowflake.schema_name
}

output "warehouse_name" {
  value = module.snowflake.warehouse_name
}

output "role_name" {
  value = module.snowflake.role_name
}

output "stage_name" {
  value = module.snowflake.stage_name
}

output "stage_path" {
  value = module.snowflake.stage_path
}

output "resource_monitor_name" {
  value = module.snowflake.resource_monitor_name
}

output "network_rule_name" {
  value = module.snowflake.network_rule_name
}

output "secret_name" {
  value = module.snowflake.secret_name
}

output "external_access_integration_name" {
  value = module.snowflake.external_access_integration_name
}

output "time_travel_retention_days" {
  value = module.snowflake.time_travel_retention_days
}
