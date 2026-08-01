terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # provider の設定（region / default_tags）はここに書かない。
  # 環境側（environments/aws-dev/versions.tf）が持つ。
}
