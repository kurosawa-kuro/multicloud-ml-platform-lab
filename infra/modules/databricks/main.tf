# Databricks 用: Catalog / Schema / Grants / Cluster・Policy / Job / Registered Model / Serving Endpoint
#
# SDK に残る: ジョブ実行トリガ / MLflow run / モデルバージョン昇格。構造仮説=Terraform 網羅度が最大
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
#   TMP/terraform-databricks-unity-catalog（data-platform-hq）
#     -> catalog / schema / grants を dynamic "grant" で回す型と force_destroy = true
#   TMP/mlops-stacks（databricks）
#     -> **権限リストのみ**: UC 登録には USE_CATALOG / USE_SCHEMA / MODIFY /
#        CREATE_MODEL / CREATE_TABLE が要る（README.md L95）。Asset Bundles 構成は移植しない
#   TMP/terraform-provider-databricks/docs/resources/{job,model_serving}.md
#     -> serverless の python_wheel_task は environment_key 必須（job.md L146）/
#        serving は entity_name + entity_version + workload_size + scale_to_zero_enabled
#
# 本モジュールはワークスペース自体を作らない（account-level provider は使わない）。
# トライアル / 既存ワークスペースに対して workspace-level のオブジェクトだけを敷く。

locals {
  # UC の識別子はハイフン不可
  name_prefix  = "${var.project_name}_${var.environment}"
  catalog_name = var.catalog_name != "" ? var.catalog_name : local.name_prefix

  # catalog を作る場合は作った側を、既存を使う場合は変数側を正とする
  catalog = var.create_catalog ? databricks_catalog.main[0].name : local.catalog_name

  volume_root = "/Volumes/${local.catalog}/${databricks_schema.main.name}/${databricks_volume.artifacts.name}"
  # dist 名は pyproject の project.name 由来（DatabricksConfig.wheel_filename と揃える）
  wheel_path = var.wheel_path != "" ? var.wheel_path : "${local.volume_root}/dist/multicloud_ml_platform_lab-0.1.0-py3-none-any.whl"

  model_full_name = "${local.catalog}.${databricks_schema.main.name}.${var.model_name}"

  grants_enabled  = var.job_principal != ""
  serving_enabled = var.serving_model_version != ""
}

# ----- Unity Catalog: catalog / schema / volume -----
#
# force_destroy を外すと、配下にオブジェクトが残った時点でフェーズ末の destroy が失敗する。

resource "databricks_catalog" "main" {
  count = var.create_catalog ? 1 : 0

  name          = local.catalog_name
  comment       = "multicloud-ml-platform-lab (${var.environment})"
  force_destroy = true

  properties = {
    purpose = "comparison-lab"
  }
}

resource "databricks_schema" "main" {
  catalog_name  = local.catalog
  name          = var.schema_name
  comment       = "California Housing models and artifacts"
  force_destroy = true
}

# wheel（Tier B の統一単位）と JSONL fallback の置き場。
# Tier A のオブジェクトストレージに相当するものが UC の Volume。
resource "databricks_volume" "artifacts" {
  catalog_name = local.catalog
  schema_name  = databricks_schema.main.name
  name         = var.volume_name
  volume_type  = "MANAGED"
  comment      = "wheel distribution and JSONL fallback"
}

# ----- Grants -----
#
# 権限セットは databricks/mlops-stacks の実運用値。
# CREATE_MODEL は provider docs の列挙に無いが UC の実権限で、API へそのまま渡る。

resource "databricks_grants" "catalog" {
  count = local.grants_enabled && var.create_catalog ? 1 : 0

  catalog = databricks_catalog.main[0].name

  grant {
    principal  = var.job_principal
    privileges = var.catalog_privileges
  }
}

resource "databricks_grants" "schema" {
  count = local.grants_enabled ? 1 : 0

  schema = databricks_schema.main.id

  grant {
    principal  = var.job_principal
    privileges = var.schema_privileges
  }
}

resource "databricks_grants" "volume" {
  count = local.grants_enabled ? 1 : 0

  volume = databricks_volume.artifacts.id

  grant {
    principal  = var.job_principal
    privileges = var.volume_privileges
  }
}

# ----- Model Registry（UC の3階層名前空間） -----
#
# 器は Terraform。バージョンの登録・昇格は SDK（学習成果物依存）。

resource "databricks_registered_model" "main" {
  name         = var.model_name
  catalog_name = local.catalog
  schema_name  = databricks_schema.main.name
  comment      = "California Housing regressor (Tier B / Databricks)"
}

resource "databricks_grants" "model" {
  count = local.grants_enabled ? 1 : 0

  model = databricks_registered_model.main.id

  grant {
    principal  = var.job_principal
    privileges = var.model_privileges
  }
}

# ----- Cluster Policy（コストガード） -----
#
# databricks_budget は account-level provider 専用なので、workspace-level では
# ポリシーで自動終了とワーカー数を縛る。クラシッククラスタを使う場合の保険。

resource "databricks_cluster_policy" "lab" {
  name = "${local.name_prefix}_lab"

  definition = jsonencode({
    "autotermination_minutes" = {
      type  = "fixed"
      value = var.cluster_policy_autotermination_minutes
    }
    "num_workers" = {
      type     = "range"
      maxValue = var.cluster_policy_max_workers
    }
    "custom_tags.Project" = {
      type  = "fixed"
      value = "multicloud-ml-platform-lab"
    }
  })
}

# ----- Job（serverless + wheel） -----
#
# クラスタ指定を書かないと serverless で動く（job.md L161）。
# serverless の python_wheel_task は environment_key が必須（job.md L146）。
# 依存の配布は environment.spec.dependencies に Volume 上の wheel パスを書く。

resource "databricks_job" "train" {
  name                = "${local.name_prefix}_train"
  description         = "California Housing training (same wheel as other platforms)"
  max_concurrent_runs = 1

  task {
    task_key        = "train"
    environment_key = "default"
    timeout_seconds = var.job_timeout_seconds

    python_wheel_task {
      package_name = var.job_package_name
      entry_point  = var.job_entry_point
    }
  }

  environment {
    environment_key = "default"

    spec {
      environment_version = var.environment_version
      dependencies        = concat([local.wheel_path], var.extra_dependencies)
    }
  }
}

# ----- Serving Endpoint -----
#
# entity_version が要るのでモデルバージョン登録後の 2 段階目
# （SageMaker の Model 以降と同じ非対称性。Vertex は器だけ先に作れる）。
# scale_to_zero_enabled = true がアイドル課金を構造的に抑える Tier B の要。

resource "databricks_model_serving" "main" {
  count = local.serving_enabled ? 1 : 0

  name = "${local.name_prefix}_serving"

  config {
    served_entities {
      name                  = "current"
      entity_name           = local.model_full_name
      entity_version        = var.serving_model_version
      workload_size         = var.serving_workload_size
      scale_to_zero_enabled = true
    }

    traffic_config {
      routes {
        served_model_name  = "current"
        traffic_percentage = 100
      }
    }
  }
}
