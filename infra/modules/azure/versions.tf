terraform {
  required_version = ">= 1.9"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # provider の設定（subscription_id / features）はここに書かない。
  # 環境側（environments/azure-dev/versions.tf）が持つ。
  # 流用元（microsoft/azureml-terraform-examples）は azurerm >= 2.26 想定で、
  # v4 系では provider に subscription_id が必要になるなど破壊的変更がある。
}
