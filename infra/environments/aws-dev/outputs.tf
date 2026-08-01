# apply 直後に adapter と check_residual.py へ渡す値をここへ集約する。
#
#   terraform -chdir=infra/environments/aws-dev output -json > artifacts/aws-dev.outputs.json

output "region" {
  value = module.aws.region
}

output "account_id" {
  value = module.aws.account_id
}

output "s3_bucket" {
  value = module.aws.s3_bucket
}

output "s3_bucket_uri" {
  value = module.aws.s3_bucket_uri
}

output "ecr_repository_urls" {
  value = module.aws.ecr_repository_urls
}

output "sagemaker_execution_role_arn" {
  value = module.aws.sagemaker_execution_role_arn
}

output "model_package_group_name" {
  value = module.aws.model_package_group_name
}

output "sagemaker_model_name" {
  value = module.aws.sagemaker_model_name
}

output "endpoint_config_name" {
  value = module.aws.endpoint_config_name
}

output "endpoint_name" {
  value = module.aws.endpoint_name
}

output "budget_enabled" {
  value = module.aws.budget_enabled
}
