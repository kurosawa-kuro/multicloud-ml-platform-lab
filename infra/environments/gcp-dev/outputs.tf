# apply 直後に adapter と check_residual.py へ渡す値をここへ集約する。
#
#   terraform -chdir=infra/environments/gcp-dev output -json > artifacts/gcp-dev.outputs.json

output "project_id" {
  value = module.gcp.project_id
}

output "region" {
  value = module.gcp.region
}

output "gcs_bucket" {
  value = module.gcp.gcs_bucket
}

output "gcs_bucket_uri" {
  value = module.gcp.gcs_bucket_uri
}

output "artifact_registry_repository" {
  value = module.gcp.artifact_registry_repository
}

output "container_image_prefix" {
  value = module.gcp.container_image_prefix
}

output "vertex_service_account_email" {
  value = module.gcp.vertex_service_account_email
}

output "vertex_endpoint_id" {
  value = module.gcp.vertex_endpoint_id
}

output "vertex_endpoint_display_name" {
  value = module.gcp.vertex_endpoint_display_name
}

output "budget_enabled" {
  value = module.gcp.budget_enabled
}
