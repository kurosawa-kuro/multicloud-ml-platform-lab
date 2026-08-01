terraform {
  required_version = ">= 1.9"

  required_providers {
    snowflake = {
      source  = "snowflakedb/snowflake"
      version = "~> 2.0"
    }
  }
}

# 認証は Doppler 経由の環境変数のみ:
#   SNOWFLAKE_ORGANIZATION_NAME / SNOWFLAKE_ACCOUNT_NAME / SNOWFLAKE_USER /
#   SNOWFLAKE_PASSWORD（または SNOWFLAKE_PRIVATE_KEY）/ SNOWFLAKE_ROLE
# credentials をコードにも tfvars にも書かない。
#
# preview_features_enabled は **空のまま**にしてある。
# プレビュー機能はメジャー版内でも破壊的変更があるため、安定版リソースだけで構成した
# （プレビュー扱いの snowflake_stage ではなく snowflake_stage_internal を使う理由）。
# 追加が必要になったら docs/tasks/02_backlog/snowflake基盤のterraform実装.md に理由を残してから足す。
provider "snowflake" {
  preview_features_enabled = []
}
