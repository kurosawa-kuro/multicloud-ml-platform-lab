# SageMaker AI 用: S3 / ECR / IAM Role / Model Package Group / Endpoint Config / Endpoint
#
# adapter（src/platforms/sagemaker/）と check_residual.py が参照する ID を出す。
# 出力していないリソースは残留検査から漏れる。

output "region" {
  description = "AWS リージョン"
  value       = var.region
}

output "account_id" {
  description = "AWS アカウント ID（data source 解決。コードに直書きしない）"
  value       = data.aws_caller_identity.current.account_id
}

output "s3_bucket" {
  description = "成果物・JSONL fallback バケット名"
  value       = aws_s3_bucket.data.id
}

output "s3_bucket_uri" {
  description = "adapter が input / output path の親として使う URI"
  value       = "s3://${aws_s3_bucket.data.id}"
}

output "ecr_repository_urls" {
  description = "用途（training / serving）→ ECR URL"
  value       = { for k, v in aws_ecr_repository.images : k => v.repository_url }
}

output "sagemaker_execution_role_arn" {
  description = "Training Job / Endpoint が引き受ける実行ロール"
  value       = aws_iam_role.sagemaker_exec.arn
}

output "model_package_group_name" {
  description = "Model Registry の器。版の登録・承認は SDK 側"
  value       = aws_sagemaker_model_package_group.main.model_package_group_name
}

output "sagemaker_model_name" {
  description = "2 段階目で作られる Model 名。学習前は null"
  value       = local.deploy_enabled ? aws_sagemaker_model.main[0].name : null
}

output "endpoint_config_name" {
  description = "2 段階目で作られる Endpoint Config 名。学習前は null"
  value       = local.deploy_enabled ? aws_sagemaker_endpoint_configuration.main[0].name : null
}

output "endpoint_name" {
  description = "2 段階目で作られる Endpoint 名。学習前は null（常時課金・destroy 必須）"
  value       = local.deploy_enabled ? aws_sagemaker_endpoint.main[0].name : null
}

output "budget_enabled" {
  description = "予算アラートを作ったか（通知先 email 未設定なら false）"
  value       = var.budget_notification_email != ""
}
