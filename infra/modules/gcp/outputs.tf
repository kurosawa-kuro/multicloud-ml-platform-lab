# Vertex AI 用: API 有効化 / GCS / Artifact Registry / IAM / Endpoint（器）/ 予算アラート
#
# adapter（src/platforms/vertex/）と check_residual.py が参照する ID を出す。
# 出力していないリソースは残留検査から漏れる。

output "project_id" {
  description = "GCP プロジェクト ID"
  value       = var.project_id
}

output "region" {
  description = "Vertex AI / GCS / Artifact Registry のロケーション"
  value       = var.region
}

output "gcs_bucket" {
  description = "成果物・JSONL fallback バケット名"
  value       = google_storage_bucket.artifacts.name
}

output "gcs_bucket_uri" {
  description = "adapter が staging / AIP_MODEL_DIR の親として使う URI"
  value       = "gs://${google_storage_bucket.artifacts.name}"
}

output "artifact_registry_repository" {
  description = "Docker リポジトリの完全リソース名"
  value       = google_artifact_registry_repository.images.name
}

output "container_image_prefix" {
  description = "docker push / CustomJob の image_uri 接頭辞"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "vertex_service_account_email" {
  description = "CustomJob / Endpoint が引き受ける実行 SA"
  value       = google_service_account.vertex_runner.email
}

output "vertex_endpoint_id" {
  description = "Endpoint（器）の完全リソース名。器を作らない設定なら null"
  value       = var.enable_endpoint_shell ? google_vertex_ai_endpoint.main[0].id : null
}

output "vertex_endpoint_display_name" {
  description = "adapter が Endpoint を探索するときのキー"
  value       = var.enable_endpoint_shell ? google_vertex_ai_endpoint.main[0].display_name : null
}

output "budget_enabled" {
  description = "予算アラートを作ったか（billing_account_id 未設定なら false）"
  value       = var.billing_account_id != ""
}
