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
}

# 認証は Doppler 経由の環境変数のみ（ARM_SUBSCRIPTION_ID / ARM_TENANT_ID / ...）。
# subscription_id は変数が空なら null にして env var へ委ねる（azurerm v4 は
# provider か環境変数のどちらかに subscription_id を要求する）。
provider "azurerm" {
  subscription_id = var.subscription_id != "" ? var.subscription_id : null

  features {
    key_vault {
      # フェーズ末に destroy したら論理削除も消す。
      # 消さないと同名 Key Vault の再作成が VaultAlreadyExists で落ちる。
      purge_soft_delete_on_destroy    = true
      recover_soft_deleted_key_vaults = false
    }

    resource_group {
      # RG 配下に想定外のリソースが残っていたら destroy を失敗させる
      # （残留を静かに握り潰すと check_residual.py の一次データが歪む）
      prevent_deletion_if_contains_resources = true
    }
  }
}
