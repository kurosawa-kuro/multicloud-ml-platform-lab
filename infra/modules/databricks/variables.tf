# Databricks 用: Catalog / Schema / Grants / Cluster・Policy / Job / Registered Model / Serving Endpoint

variable "project_name" {
  description = "リソース名の接頭辞。命名は locals で一元化する"
  type        = string
}

variable "environment" {
  description = "dev / stg / prd"
  type        = string
  default     = "dev"
}

# ----- Unity Catalog 名前空間（catalog.schema.model の3階層） -----

variable "create_catalog" {
  description = "カタログを作るか。メタストア未割り当てのトライアル環境では false にして既存カタログ名を渡す"
  type        = bool
  default     = true
}

variable "catalog_name" {
  description = "カタログ名。空なら locals で導出する（UC 名はハイフン不可）"
  type        = string
  default     = ""
}

variable "schema_name" {
  description = "スキーマ名。モデルと成果物をここに置く"
  type        = string
  default     = "ml"
}

variable "volume_name" {
  description = "wheel と JSONL fallback を置く managed volume"
  type        = string
  default     = "artifacts"
}

variable "model_name" {
  description = "UC 登録モデル名（catalog.schema.model の3つ目）"
  type        = string
  default     = "california_housing"
}

# ----- 権限（mlops-stacks の実測値） -----

variable "job_principal" {
  description = "ジョブ実行・モデル登録を行う principal（ユーザー email / グループ名 / サービスプリンシパル app ID）。空なら grants を作らない"
  type        = string
  default     = ""
}

variable "catalog_privileges" {
  description = "カタログレベル権限。databricks/mlops-stacks が UC 登録に要求する集合"
  type        = list(string)
  default     = ["USE_CATALOG"]
}

variable "schema_privileges" {
  description = "スキーマレベル権限。CREATE_MODEL は provider docs の列挙には無いが UC の実権限（API へそのまま渡る）"
  type        = list(string)
  default     = ["USE_SCHEMA", "MODIFY", "CREATE_MODEL", "CREATE_TABLE"]
}

variable "volume_privileges" {
  description = "wheel の読み書きに要る権限"
  type        = list(string)
  default     = ["READ_VOLUME", "WRITE_VOLUME"]
}

variable "model_privileges" {
  description = "登録モデルに対する権限"
  type        = list(string)
  default     = ["EXECUTE", "APPLY_TAG"]
}

# ----- ジョブ（wheel 配布 = Tier B の統一単位） -----

variable "wheel_path" {
  description = "Volume 上の wheel パス。空なら locals で volume 配下の既定パスを導出する"
  type        = string
  default     = ""
}

variable "job_package_name" {
  description = "python_wheel_task の package_name。**pyproject の dist 名**と一致させる（正規化後のアンダースコア形）。ずれると entry point を引けず起動しない"
  type        = string
  default     = "multicloud_ml_platform_lab"
}

variable "job_entry_point" {
  description = "wheel の entry point（console_scripts 名）。pyproject の [project.scripts] と一致させる"
  type        = string
  default     = "train"
}

variable "environment_version" {
  description = <<-EOT
    serverless environment のクライアント版。**Python 版がこれで決まる**。
    version 1=3.10 / 2=3.11 / 3〜5=3.12（2026-08-01 時点の release notes）。
    本ラボの wheel は requires-python >=3.12 なので **3 以上が必須**。
    2 のままだと wheel の install が ERROR_UNSUPPORTED_PYTHON_VERSION で落ちる（実測）。
  EOT
  type        = string
  default     = "3"
}

variable "extra_dependencies" {
  description = "wheel 以外に serverless へ入れる pip 依存（requirements 行）"
  type        = list(string)
  default     = []
}

variable "job_timeout_seconds" {
  description = "1 回の実行の上限。無限に回して課金が伸びるのを防ぐ"
  type        = number
  default     = 3600
}

# ----- Serving Endpoint -----

variable "serving_model_version" {
  description = "配信するモデルバージョン。空ならエンドポイントを作らない（バージョンは SDK が学習後に登録する）"
  type        = string
  default     = ""
}

variable "serving_workload_size" {
  description = "Small で足りる（比較は 1 件推論）"
  type        = string
  default     = "Small"
}

# ----- コストガード（Cluster Policy） -----
#
# databricks_budget は account-level provider 専用なので、workspace-level では
# クラスタポリシーで上限を縛る。

variable "cluster_policy_max_workers" {
  description = "クラスタポリシーで許可する最大ワーカー数"
  type        = number
  default     = 2
}

variable "cluster_policy_autotermination_minutes" {
  description = "自動終了までの分数。必須化してアイドル課金を防ぐ"
  type        = number
  default     = 20
}
