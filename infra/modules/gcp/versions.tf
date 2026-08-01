terraform {
  required_version = ">= 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  # provider の設定（project / region）はここに書かない。
  # 環境側（environments/gcp-dev/versions.tf）が持つ。
}
