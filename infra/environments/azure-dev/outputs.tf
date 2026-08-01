# apply 直後に adapter と check_residual.py へ渡す値をここへ集約する。
#
#   terraform -chdir=infra/environments/azure-dev output -json > artifacts/azure-dev.outputs.json

output "location" {
  value = module.azure.location
}

output "subscription_id" {
  value = module.azure.subscription_id
}

output "resource_group_name" {
  value = module.azure.resource_group_name
}

output "workspace_name" {
  value = module.azure.workspace_name
}

output "workspace_id" {
  value = module.azure.workspace_id
}

output "compute_cluster_name" {
  value = module.azure.compute_cluster_name
}

output "storage_account_name" {
  value = module.azure.storage_account_name
}

output "key_vault_name" {
  value = module.azure.key_vault_name
}

output "application_insights_name" {
  value = module.azure.application_insights_name
}

output "container_registry_login_server" {
  value = module.azure.container_registry_login_server
}

output "budget_enabled" {
  value = module.azure.budget_enabled
}
