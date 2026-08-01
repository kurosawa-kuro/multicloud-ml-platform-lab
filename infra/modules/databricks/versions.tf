terraform {
  required_version = ">= 1.9"

  required_providers {
    databricks = {
      source  = "databricks/databricks"
      version = "~> 1.0"
    }
  }

  # provider の設定（host / token）はここに書かない。
  # 環境側（environments/dbx-dev/versions.tf）が持つ。
  # 本モジュールは **workspace-level provider 専用**。account-level を要求する
  # リソース（databricks_budget / databricks_mws_*）は含めない。
}
