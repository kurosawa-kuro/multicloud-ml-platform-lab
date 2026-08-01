# azure-dev : modules/azure の dev 環境インスタンス。
#
# フェーズ末に必ず destroy する（Tier A のマネージドエンドポイントは常時課金）。
# destroy 後の残留は scripts/check_residual.py で列挙し infra_events へ記録する。
# Azure 固有の残留候補: Key Vault の論理削除 / Storage Account 内の blob。

module "azure" {
  source = "../../modules/azure"

  project_name = var.project_name
  environment  = var.environment

  location                    = var.location
  compute_cluster_vm_size     = var.compute_cluster_vm_size
  compute_cluster_vm_priority = var.compute_cluster_vm_priority

  budget_amount             = var.budget_amount
  budget_start_date         = var.budget_start_date
  budget_notification_email = var.budget_notification_email
}
