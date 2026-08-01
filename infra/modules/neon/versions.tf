terraform {
  required_version = ">= 1.9"

  # TODO(Phase): required_providers
  # Snowflake provider は source 名リネーム済み・プレビュー機能は既定無効
  # （preview_features_enabled に明示追加。メジャー版内でも破壊的変更あり）
}
