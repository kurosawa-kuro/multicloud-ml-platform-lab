terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# 認証は Doppler 経由の環境変数のみ（~/.aws/ は使わない）。
# credentials をコードにも tfvars にも書かない。
provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "multicloud-ml-platform-lab"
      Environment = "dev"
      Platform    = "sagemaker"
      ManagedBy   = "Terraform"
    }
  }
}
