# Databricks 用: Catalog / Schema / Grants / Cluster・Policy / Job / Registered Model / Serving Endpoint
#
# adapter（src/platforms/databricks/）と check_residual.py が参照する ID を出す。
# 出力していないリソースは残留検査から漏れる。

output "catalog_name" {
  description = "UC カタログ名（作った場合も既存を使った場合もこれが正）"
  value       = local.catalog
}

output "schema_name" {
  description = "UC スキーマ名"
  value       = databricks_schema.main.name
}

output "volume_path" {
  description = "wheel と JSONL fallback の置き場（/Volumes/...）"
  value       = local.volume_root
}

output "wheel_path" {
  description = "ジョブが依存として読む wheel のパス。make のアップロード先でもある"
  value       = local.wheel_path
}

output "model_full_name" {
  description = "catalog.schema.model の3階層名。SDK の版登録先"
  value       = local.model_full_name
}

output "job_id" {
  description = "adapter が実行をトリガするジョブ ID"
  value       = databricks_job.train.id
}

output "job_url" {
  description = "ジョブ画面 URL（失敗調査の入口）"
  value       = databricks_job.train.url
}

output "cluster_policy_id" {
  description = "クラシッククラスタを使う場合に適用するポリシー"
  value       = databricks_cluster_policy.lab.id
}

output "serving_endpoint_name" {
  description = "Serving Endpoint 名。モデルバージョン未登録なら null"
  value       = local.serving_enabled ? databricks_model_serving.main[0].name : null
}

output "grants_enabled" {
  description = "grants を作ったか（job_principal 未設定なら false）"
  value       = local.grants_enabled
}
