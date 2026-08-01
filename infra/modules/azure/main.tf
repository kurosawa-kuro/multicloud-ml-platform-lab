# Azure ML 用: Resource Group / Storage / ACR / Key Vault / App Insights / Workspace / Compute Cluster
#
# SDK に残る: Command Job / Model 登録 / Managed Online Endpoint 更新
#
# 境界の原則（docs/02_architecture.md「境界」）:
#   静的基盤 = Terraform / ジョブ実行・登録・デプロイ = SDK・CLI・SQL
#   terraform apply に学習実行を含めない。state に ML 実行履歴が混ざると
#   インフラ状態と実行履歴の両方の再現性が落ちる。
#
# 「Terraform でどこまで書けたか」自体が比較軸なので、
# 書けなかったもの・SDK に逃がしたものは docs/comparison/ に必ず残す。
#
# 移植元: TMP/azureml-terraform-examples/100-simple-deployment（microsoft・MIT）
#   -> RG / Storage / Key Vault / App Insights / ACR / Workspace の依存の張り方と
#      random_string によるグローバル一意名。Private Link なしの最小シナリオのみ。
#   参考: TMP/terraform-azurerm-avm-res-machinelearningservices-workspace（AVM の変数の切り方）
#
# 移植時の差分（そのままでは動かない・残る点）:
#   1. 流用元は azurerm >= 2.26（2021）。ここは ~> 4.0 に合わせている。
#   2. 流用元は Compute Cluster を null_resource + `az ml computetarget create` の
#      local-exec で作る。当時 provider 未対応だったため。ここでは
#      azurerm_machine_learning_compute_cluster に置き換えた。local-exec のままだと
#      state が実体を把握できず、残留検査（check_residual.py）から漏れる。
#   3. AKS（aks.tf）は移植しない。推論は Managed Online Endpoint で、AKS は
#      本ラボの Golden Path 上に無い。
#   4. Key Vault は purge protection を有効にしない（有効だと destroy 後も論理削除で
#      残り、同名再作成が VaultAlreadyExists で落ちる）。

data "azurerm_client_config" "current" {}

# Storage Account / ACR の名前はグローバル一意かつ英数字のみ
resource "random_string" "suffix" {
  length  = 6
  special = false
  upper   = false
  numeric = true
}

locals {
  name_prefix = "${var.project_name}-${var.environment}"
  # Storage Account は 3-24 文字・小文字英数字のみ（ハイフン不可）
  name_prefix_compact = "${var.project_name}${var.environment}"

  resource_group_name = var.resource_group_name != "" ? var.resource_group_name : "${local.name_prefix}-rg"

  names = {
    storage_account    = "${local.name_prefix_compact}sa${random_string.suffix.result}"
    key_vault          = "${local.name_prefix}-kv-${random_string.suffix.result}"
    app_insights       = "${local.name_prefix}-ai-${random_string.suffix.result}"
    container_registry = "${local.name_prefix_compact}acr${random_string.suffix.result}"
    workspace          = "${local.name_prefix}-ws-${random_string.suffix.result}"
    compute_cluster    = "${local.name_prefix}-cpu"
    budget             = "${local.name_prefix}-monthly-guardrail"
  }

  common_tags = {
    Project     = "multicloud-ml-platform-lab"
    Environment = var.environment
    Platform    = "azureml"
    ManagedBy   = "Terraform"
  }
}

resource "azurerm_resource_group" "main" {
  name     = local.resource_group_name
  location = var.location

  tags = local.common_tags
}

# ----- Workspace の必須依存: Storage Account / Key Vault -----

resource "azurerm_storage_account" "main" {
  name                     = local.names.storage_account
  resource_group_name      = azurerm_resource_group.main.name
  location                 = azurerm_resource_group.main.location
  account_tier             = "Standard"
  account_replication_type = var.storage_replication_type

  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false

  tags = local.common_tags
}

# purge protection は入れない。入れるとフェーズ末の destroy 後も論理削除で残り、
# 同名の再作成が VaultAlreadyExists で落ちる（環境側の features で purge も有効にする）。
resource "azurerm_key_vault" "main" {
  name                       = local.names.key_vault
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  soft_delete_retention_days = var.key_vault_soft_delete_retention_days
  purge_protection_enabled   = false

  tags = local.common_tags
}

# ----- App Insights（必須依存） / ACR（任意依存） -----
#
# azurerm v4.81.0 のスキーマでは application_insights_id は Workspace の必須引数、
# container_registry_id は任意（docs/tasks/02_backlog/azureml-workspace-dependency-check.md の実測）。
# ACR は BYOC（Tier A の統一単位）に要るので既定で作る。

resource "azurerm_application_insights" "main" {
  name                = local.names.app_insights
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  application_type    = "web"

  tags = local.common_tags
}

resource "azurerm_container_registry" "main" {
  count = var.create_container_registry ? 1 : 0

  name                = local.names.container_registry
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = var.acr_sku

  # 管理者ユーザーは使わない（イメージの push は az acr login、pull は managed identity）
  admin_enabled = false

  tags = local.common_tags
}

# ----- Workspace -----

resource "azurerm_machine_learning_workspace" "main" {
  name                = local.names.workspace
  friendly_name       = local.name_prefix
  description         = "California Housing comparison lab (Tier A / Azure ML)"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location

  storage_account_id      = azurerm_storage_account.main.id
  key_vault_id            = azurerm_key_vault.main.id
  application_insights_id = azurerm_application_insights.main.id
  container_registry_id   = var.create_container_registry ? azurerm_container_registry.main[0].id : null

  public_network_access_enabled = var.public_network_access_enabled

  identity {
    type = "SystemAssigned"
  }

  tags = local.common_tags
}

# BYOC イメージを pull するために Workspace のマネージド ID へ AcrPull を与える
resource "azurerm_role_assignment" "workspace_acr_pull" {
  count = var.create_container_registry ? 1 : 0

  scope                = azurerm_container_registry.main[0].id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_machine_learning_workspace.main.identity[0].principal_id
}

# ----- Compute Cluster -----
#
# min_node_count = 0 + idle scale down が必須。1 以上にするとジョブが無くても課金される。
# 流用元の null_resource + az CLI ではなく Terraform リソースで持つ（state に載せて残留検査に通す）。

resource "azurerm_machine_learning_compute_cluster" "main" {
  name                          = local.names.compute_cluster
  location                      = azurerm_resource_group.main.location
  machine_learning_workspace_id = azurerm_machine_learning_workspace.main.id
  vm_priority                   = var.compute_cluster_vm_priority
  vm_size                       = var.compute_cluster_vm_size

  scale_settings {
    min_node_count                       = var.compute_cluster_min_nodes
    max_node_count                       = var.compute_cluster_max_nodes
    scale_down_nodes_after_idle_duration = var.compute_cluster_idle_duration
  }

  identity {
    type = "SystemAssigned"
  }

  tags = local.common_tags
}

# ----- 予算アラート -----
#
# Tier A は各 ¥2,000/月（docs/01_requirements.md）。
# azurerm の予算は通貨を指定できない（サブスクリプションの請求通貨で解釈される）。
# GCP は JPY 明示、AWS は USD 固定で、3基盤とも通貨の扱いが違う。

resource "azurerm_consumption_budget_resource_group" "monthly_guardrail" {
  count = var.budget_notification_email == "" ? 0 : 1

  name              = local.names.budget
  resource_group_id = azurerm_resource_group.main.id
  amount            = var.budget_amount
  time_grain        = "Monthly"

  time_period {
    start_date = var.budget_start_date
  }

  notification {
    enabled        = true
    threshold      = 50
    operator       = "GreaterThan"
    threshold_type = "Actual"
    contact_emails = [var.budget_notification_email]
  }

  notification {
    enabled        = true
    threshold      = 90
    operator       = "GreaterThan"
    threshold_type = "Actual"
    contact_emails = [var.budget_notification_email]
  }

  notification {
    enabled        = true
    threshold      = 100
    operator       = "GreaterThan"
    threshold_type = "Forecasted"
    contact_emails = [var.budget_notification_email]
  }
}
