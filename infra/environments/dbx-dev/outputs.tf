# apply 直後に adapter と check_residual.py へ渡す値をここへ集約する。
#
#   terraform -chdir=infra/environments/dbx-dev output -json > artifacts/dbx-dev.outputs.json

output "catalog_name" {
  value = module.databricks.catalog_name
}

output "schema_name" {
  value = module.databricks.schema_name
}

output "volume_path" {
  value = module.databricks.volume_path
}

output "wheel_path" {
  value = module.databricks.wheel_path
}

output "model_full_name" {
  value = module.databricks.model_full_name
}

output "job_id" {
  value = module.databricks.job_id
}

output "job_url" {
  value = module.databricks.job_url
}

output "cluster_policy_id" {
  value = module.databricks.cluster_policy_id
}

output "serving_endpoint_name" {
  value = module.databricks.serving_endpoint_name
}

output "grants_enabled" {
  value = module.databricks.grants_enabled
}
