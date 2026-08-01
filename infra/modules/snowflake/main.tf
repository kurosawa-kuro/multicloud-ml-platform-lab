# Snowflake 用: Database / Schema / Warehouse / Role / Grants / Stage / Network Rule / Secret / External Access Integration
#
# SQL/SDK に残る: Stored Procedure 実行 / Model Registry 登録 / サービス関数作成
#
# 境界の原則（docs/02_architecture.md「境界」）:
#   静的基盤 = Terraform / ジョブ実行・登録・デプロイ = SDK・CLI・SQL
#   terraform apply に学習実行を含めない。state に ML 実行履歴が混ざると
#   インフラ状態と実行履歴の両方の再現性が落ちる。
#
# 「Terraform でどこまで書けたか」自体が比較軸なので、
# 書けなかったもの・SDK に逃がしたものは docs/comparison/ に必ず残す。
#
# 移植元:
#   TMP/terraform-snowflake-role（getindata）
#     -> snowflake_grant_privileges_to_account_role を on_account_object / on_schema /
#        on_schema_object で **組ごとに別リソース** として書く型（まとめると相互上書きする）
#   TMP/snowflake-terraform-kit（tekumara）
#     -> warehouse + resource monitor（credit_quota / auto_suspend）の組み合わせ**設計のみ**。
#        provider が chanzuckerberg/snowflake ~> 0.29 と古く、snowflake_warehouse_grant など
#        削除済みリソースを使っているため構文は移植しない
#   TMP/terraform-provider-snowflake/docs（snowflakedb・実装根拠）
#
# 発見（比較レポート行き）: External Access Integration には **Terraform ネイティブリソースが無い**
#   （provider の docs/resources 141 件を走査済み）。ここでは snowflake_execute で
#   CREATE / DROP を持ち、state と destroy 経路から漏れないようにしている。

locals {
  # 未クォート識別子は大文字に正規化されるので最初から大文字で揃える
  name_prefix   = upper("${var.project_name}_${var.environment}")
  database_name = var.database_name != "" ? upper(var.database_name) : local.name_prefix
  schema_name   = upper(var.schema_name)
  role_name     = var.role_name != "" ? upper(var.role_name) : "${local.name_prefix}_ROLE"

  warehouse_name        = "${local.name_prefix}_WH"
  resource_monitor_name = "${local.name_prefix}_MONITOR"
  network_rule_name     = "${local.name_prefix}_NEON_EGRESS"
  secret_name           = "${local.name_prefix}_NEON_SECRET"
  eai_name              = "${local.name_prefix}_NEON_EAI"

  qualified_schema = "\"${local.database_name}\".\"${local.schema_name}\""

  # 外部ネットワークアクセスは3点セット（network rule / secret / EAI）。
  # host が無ければ 1 つも作らない。
  external_access_enabled = var.neon_host != ""
  secret_enabled          = local.external_access_enabled && var.create_neon_secret
  eai_enabled             = local.external_access_enabled && var.create_external_access_integration
}

# ----- Database / Schema -----
#
# Time Travel は Tier A に無い種類の残留。既定を最小にして縮める（Fail-safe 7 日は消せない）。

resource "snowflake_database" "main" {
  name                        = local.database_name
  comment                     = "multicloud-ml-platform-lab (${var.environment})"
  data_retention_time_in_days = var.data_retention_time_in_days
}

resource "snowflake_schema" "main" {
  database                    = snowflake_database.main.name
  name                        = local.schema_name
  comment                     = "California Housing models, procedures and stage"
  data_retention_time_in_days = var.data_retention_time_in_days
}

# ----- Warehouse + Resource Monitor（コストガード） -----
#
# Tier A の予算アラートに相当するが、Resource Monitor は「通知」ではなく「停止」までできる。
# 作成には ACCOUNTADMIN が要るので、権限が無いトライアルでは create_resource_monitor = false。

resource "snowflake_resource_monitor" "main" {
  count = var.create_resource_monitor ? 1 : 0

  name            = local.resource_monitor_name
  credit_quota    = var.credit_quota
  frequency       = "MONTHLY"
  start_timestamp = "IMMEDIATELY"
  notify_triggers = [50, 90]
  suspend_trigger = 100

  lifecycle {
    ignore_changes = [start_timestamp]
  }
}

resource "snowflake_warehouse" "main" {
  name           = local.warehouse_name
  comment        = "multicloud-ml-platform-lab compute"
  warehouse_size = var.warehouse_size

  auto_suspend        = var.warehouse_auto_suspend
  auto_resume         = "true"
  initially_suspended = true

  resource_monitor = var.create_resource_monitor ? snowflake_resource_monitor.main[0].fully_qualified_name : null
}

# ----- Stage（Tier B の統一単位 = パッケージのアップロード先） -----
#
# プレビュー扱いの snowflake_stage ではなく、安定版の snowflake_stage_internal を使う。

resource "snowflake_stage_internal" "code" {
  name     = var.stage_name
  database = snowflake_database.main.name
  schema   = snowflake_schema.main.name
  comment  = "src/core/ml package upload (Tier B unification unit)"

  directory {
    enable = true
  }
}

# ----- Role + Grants -----
#
# 権限は (対象 × 権限セット) ごとに別リソースにする。
# 同じ対象に複数の grant リソースを当てると相互に上書きし合う（getindata README の指摘）。

resource "snowflake_account_role" "main" {
  name    = local.role_name
  comment = "Runs training procedures and writes telemetry"
}

resource "snowflake_grant_privileges_to_account_role" "database" {
  account_role_name = snowflake_account_role.main.name
  privileges        = ["USAGE"]

  on_account_object {
    object_type = "DATABASE"
    object_name = snowflake_database.main.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "warehouse" {
  account_role_name = snowflake_account_role.main.name
  privileges        = ["USAGE", "OPERATE"]

  on_account_object {
    object_type = "WAREHOUSE"
    object_name = snowflake_warehouse.main.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "schema" {
  account_role_name = snowflake_account_role.main.name
  privileges        = var.schema_privileges

  on_schema {
    schema_name = local.qualified_schema
  }
}

resource "snowflake_grant_privileges_to_account_role" "stage" {
  account_role_name = snowflake_account_role.main.name

  # **内部ステージに USAGE は付けられない**（READ / WRITE のみ）。
  #   003038 (42601): Cannot grant or revoke USAGE on an internal staging location
  # 外部ステージなら USAGE を使うので、両者で必要な権限が違う（2026-08-01 実測）。
  privileges = ["READ", "WRITE"]

  on_schema_object {
    object_type = "STAGE"
    object_name = "${local.qualified_schema}.\"${snowflake_stage_internal.code.name}\""
  }
}

resource "snowflake_grant_account_role" "to_user" {
  count = var.grant_to_user == "" ? 0 : 1

  role_name = snowflake_account_role.main.name
  user_name = var.grant_to_user
}

# ----- 外部ネットワークアクセス（Neon 到達）: network rule / secret / EAI -----
#
# Tier A には無い「外部到達を宣言的オブジェクトで通す」構造。
# ここが通らなければ telemetry は JSONL fallback + make collect に落ちる
# （failure_class = 'network' として記録する。docs/02_architecture.md）。

resource "snowflake_network_rule" "neon_egress" {
  count = local.external_access_enabled ? 1 : 0

  name       = local.network_rule_name
  database   = snowflake_database.main.name
  schema     = snowflake_schema.main.name
  comment    = "Egress to Neon pooled endpoint"
  type       = "HOST_PORT"
  mode       = "EGRESS"
  value_list = ["${var.neon_host}:${var.neon_port}"]
}

resource "snowflake_secret_with_generic_string" "neon" {
  count = local.secret_enabled ? 1 : 0

  name          = local.secret_name
  database      = snowflake_database.main.name
  schema        = snowflake_schema.main.name
  secret_string = var.neon_secret_string
  comment       = "Neon connection string (value comes from Doppler via TF_VAR_)"
}

# External Access Integration は Terraform ネイティブリソースが存在しない。
# snowflake_execute で CREATE / DROP を持ち、state と destroy 経路に載せる。
# revert を書き忘れると destroy 後に残る（= 残留）。
resource "snowflake_execute" "external_access_integration" {
  count = local.eai_enabled ? 1 : 0

  execute = join(" ", [
    "CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION ${local.eai_name}",
    "ALLOWED_NETWORK_RULES = (\"${local.database_name}\".\"${local.schema_name}\".\"${local.network_rule_name}\")",
    local.secret_enabled ? "ALLOWED_AUTHENTICATION_SECRETS = (\"${local.database_name}\".\"${local.schema_name}\".\"${local.secret_name}\")" : "",
    "ENABLED = TRUE",
  ])
  revert = "DROP EXTERNAL ACCESS INTEGRATION IF EXISTS ${local.eai_name}"
  query  = "SHOW EXTERNAL ACCESS INTEGRATIONS LIKE '${local.eai_name}'"

  depends_on = [
    snowflake_network_rule.neon_egress,
    snowflake_secret_with_generic_string.neon,
  ]
}

# EAI と secret の USAGE も同じくネイティブ grant リソースの対象外なので SQL で付ける
resource "snowflake_execute" "grant_external_access_integration" {
  count = local.eai_enabled ? 1 : 0

  execute = "GRANT USAGE ON INTEGRATION ${local.eai_name} TO ROLE ${snowflake_account_role.main.name}"
  revert  = "REVOKE USAGE ON INTEGRATION ${local.eai_name} FROM ROLE ${snowflake_account_role.main.name}"

  depends_on = [snowflake_execute.external_access_integration]
}
