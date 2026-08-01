terraform {
  required_version = ">= 1.9"

  required_providers {
    snowflake = {
      # source 名はリネーム済み（旧 Snowflake-Labs/snowflake）
      source  = "snowflakedb/snowflake"
      version = "~> 2.0"
    }
  }

  # provider の設定（組織 / アカウント / 認証 / preview_features_enabled）はここに書かない。
  # 環境側（environments/sf-dev/versions.tf）が持つ。
  #
  # プレビュー機能は既定無効で、メジャー版内でも破壊的変更がある。
  # 本モジュールは **プレビュー機能を1つも使わない**（安定版の
  # snowflake_stage_internal / snowflake_network_rule / snowflake_secret_with_generic_string
  # だけで構成し、プレビュー扱いの snowflake_stage は使わない）。
}
