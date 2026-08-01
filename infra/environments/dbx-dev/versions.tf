terraform {
  required_version = ">= 1.9"

  required_providers {
    databricks = {
      source  = "databricks/databricks"
      version = "~> 1.0"
    }
  }
}

# workspace-level provider。host / token は環境変数（Doppler）で渡す:
#   DATABRICKS_HOST=https://<workspace>.cloud.databricks.com
#   DATABRICKS_TOKEN=...
# credentials をコードにも tfvars にも書かない。
provider "databricks" {}
