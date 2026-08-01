# Snowflake 用: Database / Schema / Warehouse / Role / Grants / Stage / Network Rule / Secret / External Access Integration

variable "project_name" {
  description = "リソース名の接頭辞。命名は locals で一元化する（識別子は大文字に正規化される）"
  type        = string
}

variable "environment" {
  description = "dev / stg / prd"
  type        = string
  default     = "dev"
}

# ----- 名前空間 -----

variable "database_name" {
  description = "データベース名。空なら locals で導出する"
  type        = string
  default     = ""
}

variable "schema_name" {
  description = "スキーマ名。モデル・成果物・ステージをここに置く"
  type        = string
  default     = "ML"
}

variable "stage_name" {
  description = "内部ステージ名。src/core/ml のパッケージ（Tier B の統一単位）を上げる先"
  type        = string
  default     = "CODE"
}

variable "data_retention_time_in_days" {
  description = "Time Travel の保持日数。Tier B 特有の残留を最小化するため既定 1（Fail-safe の 7 日は消せない）"
  type        = number
  default     = 1
}

# ----- Warehouse / コストガード -----

variable "warehouse_size" {
  description = "X-Small で足りる（LightGBM・California Housing）"
  type        = string
  default     = "XSMALL"
}

variable "warehouse_auto_suspend" {
  description = "アイドル自動停止までの秒数。アイドル課金の主対策なので必ず有効にする"
  type        = number
  default     = 60
}

variable "create_resource_monitor" {
  description = "クレジット上限を Resource Monitor で縛るか。作成には ACCOUNTADMIN が要る。**false にするとガードレールが無くなる**（トライアルで権限が無い場合のみ false）"
  type        = bool
  default     = true
}

variable "credit_quota" {
  description = "月次クレジット上限。トライアルのクレジットを一気に溶かさないための保険"
  type        = number
  default     = 10
}

# ----- ロール / 権限 -----

variable "role_name" {
  description = "実行ロール名。空なら locals で導出する"
  type        = string
  default     = ""
}

variable "grant_to_user" {
  description = "ロールを付与するユーザー。空なら付与しない（リポジトリに実ユーザー名を直書きしない）"
  type        = string
  default     = ""
}

variable "schema_privileges" {
  description = "スキーマレベル権限。sproc 登録・モデル登録・ステージ利用に要る集合"
  type        = list(string)
  default = [
    "USAGE",
    "CREATE PROCEDURE",
    "CREATE FUNCTION",
    "CREATE TABLE",
    "CREATE STAGE",
    "CREATE MODEL",
  ]
}

# ----- 外部ネットワークアクセス（Neon 到達） -----

variable "neon_host" {
  description = "Neon の pooled endpoint ホスト名。空なら network rule / secret / EAI を作らない"
  type        = string
  default     = ""
}

variable "neon_port" {
  description = "PostgreSQL ポート"
  type        = number
  default     = 5432
}

variable "create_neon_secret" {
  description = "Neon 資格情報の secret を作るか。値は neon_secret_string で渡す（sensitive 値を条件式に使うと output まで sensitive 汚染するため bool を分けている）"
  type        = bool
  default     = false
}

variable "neon_secret_string" {
  description = "Neon 接続文字列。Doppler から TF_VAR_neon_secret_string で渡す"
  type        = string
  default     = ""
  sensitive   = true
}

variable "create_external_access_integration" {
  description = "EAI を作るか。Terraform ネイティブリソースが存在しないため snowflake_execute（SQL 直書き）で持つ"
  type        = bool
  default     = true
}
