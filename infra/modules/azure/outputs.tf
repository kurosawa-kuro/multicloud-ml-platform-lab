# Azure ML 用: Resource Group / Storage / ACR / Key Vault / App Insights / Workspace / Compute Cluster
#
# adapter（src/platforms/azureml/）と check_residual.py が参照する ID を出す。
# 出力していないリソースは残留検査から漏れる。

output "location" {
  description = "Azure リージョン"
  value       = azurerm_resource_group.main.location
}

output "subscription_id" {
  description = "サブスクリプション ID（data source 解決。コードに直書きしない）"
  value       = data.azurerm_client_config.current.subscription_id
}

output "resource_group_name" {
  description = "残留検査の起点。ここを消せば配下は消える（消えない例外を記録する）"
  value       = azurerm_resource_group.main.name
}

output "workspace_name" {
  description = "adapter が Command Job を投げる先"
  value       = azurerm_machine_learning_workspace.main.name
}

output "workspace_id" {
  description = "Workspace のリソース ID"
  value       = azurerm_machine_learning_workspace.main.id
}

output "compute_cluster_name" {
  description = "Command Job の compute 指定に使う（min 0 台・idle scale down 済み）"
  value       = azurerm_machine_learning_compute_cluster.main.name
}

output "storage_account_name" {
  description = "既定データストア。JSONL fallback の置き場でもある"
  value       = azurerm_storage_account.main.name
}

output "key_vault_name" {
  description = "論理削除が残るので destroy 後に purge 状態を確認する"
  value       = azurerm_key_vault.main.name
}

output "application_insights_name" {
  description = "実行ログ・メトリクスの追跡先"
  value       = azurerm_application_insights.main.name
}

output "container_registry_login_server" {
  description = "docker push / Command Job の image 参照に使う。ACR を作らない設定なら null"
  value       = var.create_container_registry ? azurerm_container_registry.main[0].login_server : null
}

output "budget_enabled" {
  description = "予算アラートを作ったか（通知先 email 未設定なら false）"
  value       = var.budget_notification_email != ""
}
