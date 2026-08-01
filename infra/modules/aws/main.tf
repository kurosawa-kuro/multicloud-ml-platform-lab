# SageMaker AI 用: S3 / ECR / IAM Role / Model Package Group / Endpoint Config / Endpoint
#
# SDK に残る: Training Job / Model Registry 登録・承認
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
#   ML/eks-app-mlops-platform-v2.0/infra/terraform/prod/{locals,60_s3,80_iam}.tf
#     -> local.names 命名一元化 / S3 堅牢化セット / Sid 分割の最小権限ポリシー
#   ML/eks-app-mlops-platform-v2.0/infra/terraform/shared/10_ecr.tf
#     -> for_each + aws_ecr_lifecycle_policy（keep last N）
#   TMP/amazon-sagemaker-ml-pipeline-deploy-with-terraform（aws-samples）
#     -> sagemaker.amazonaws.com を principal とする実行ロール / BYOC 前提の ECR /
#        Training -> Model -> EndpointConfig -> Endpoint の依存順序
# 移植時の差分: 上記 aws-samples は実行ロールに AmazonSageMakerFullAccess を貼るが、
#   本プロジェクトは貼らない。permission friction（最小権限で通るまでの修正回数）が
#   本命の計測軸であり、FullAccess を貼るとその一次データが消える。

data "aws_caller_identity" "current" {}

locals {
  name_prefix = "${var.project_name}-${var.environment}"

  # バケット名はグローバル一意なので account_id を含める
  bucket_name = var.bucket_name != "" ? var.bucket_name : "${local.name_prefix}-${data.aws_caller_identity.current.account_id}"

  names = {
    s3_data          = local.bucket_name
    role_sagemaker   = "${local.name_prefix}-sagemaker-exec"
    policy_sagemaker = "${local.name_prefix}-sagemaker-policy"
    model_package    = "${local.name_prefix}-models"
    model            = "${local.name_prefix}-model"
    endpoint_config  = "${local.name_prefix}-endpoint-config"
    endpoint         = "${local.name_prefix}-endpoint"
    budget           = "${local.name_prefix}-monthly-guardrail"
    log_group_sm     = "/aws/sagemaker"
  }

  common_tags = {
    Project     = "multicloud-ml-platform-lab"
    Environment = var.environment
    Platform    = "sagemaker"
    ManagedBy   = "Terraform"
  }

  # SageMaker は Vertex と違い「器だけ」を先に作れない。
  # Endpoint <- EndpointConfig <- Model <- 学習成果物（model.tar.gz）という依存があるため、
  # 学習前は基盤だけを apply し、学習後に model_data_url を渡して 2 段階目を apply する。
  deploy_enabled = var.model_data_url != "" && var.serving_image_uri != ""
}

# ----- ストレージ: 学習データ / 成果物 / JSONL fallback -----

resource "aws_s3_bucket" "data" {
  bucket        = local.names.s3_data
  force_destroy = var.bucket_force_destroy

  tags = merge(local.common_tags, {
    Name = local.names.s3_data
  })
}

# ラボ既定は Suspended（バージョン残骸が destroy と残留検査を汚す）
resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id

  versioning_configuration {
    status = "Suspended"
  }
}

# SSE-S3（KMS キーを作るとキー自体が残留・課金対象になる）
resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket = aws_s3_bucket.data.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    id     = "abort-incomplete-multipart"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  rule {
    id     = "temp-data-lifecycle"
    status = "Enabled"

    filter {
      prefix = "temp/"
    }

    expiration {
      days = 30
    }
  }
}

resource "aws_s3_bucket_policy" "data" {
  bucket = aws_s3_bucket.data.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.data.arn,
          "${aws_s3_bucket.data.arn}/*",
        ]
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      },
    ]
  })

  depends_on = [aws_s3_bucket_public_access_block.data]
}

# ----- コンテナ: ECR は 1 repo = 1 イメージ（Artifact Registry との構造差） -----

resource "aws_ecr_repository" "images" {
  for_each = var.ecr_repositories

  name                 = "${local.name_prefix}-${each.key}"
  image_tag_mutability = each.value.image_tag_mutability
  force_delete         = var.ecr_force_delete

  image_scanning_configuration {
    scan_on_push = each.value.scan_on_push
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-${each.key}"
  })
}

resource "aws_ecr_lifecycle_policy" "images" {
  for_each = var.ecr_repositories

  repository = aws_ecr_repository.images[each.key].name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last ${each.value.max_image_count} images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = each.value.max_image_count
        }
        action = {
          type = "expire"
        }
      },
    ]
  })
}

# ----- IAM: SageMaker 実行ロール（Training Job / Endpoint が引き受ける） -----

data "aws_iam_policy_document" "sagemaker_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["sagemaker.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sagemaker_exec" {
  name               = local.names.role_sagemaker
  assume_role_policy = data.aws_iam_policy_document.sagemaker_assume_role.json

  tags = local.common_tags
}

# Sid 分割の最小権限。AmazonSageMakerFullAccess は意図的に attach しない。
# 権限不足で落ちたら「足した回数」を ml_runs.attempt / failure_class に記録する。
resource "aws_iam_policy" "sagemaker_exec" {
  name        = local.names.policy_sagemaker
  description = "Least-privilege policy for SageMaker training jobs and endpoints"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "S3Access"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket",
        ]
        Resource = [
          aws_s3_bucket.data.arn,
          "${aws_s3_bucket.data.arn}/*",
        ]
      },
      {
        # GetAuthorizationToken はリソース指定不可（AWS 側の仕様）
        Sid      = "ECRAuth"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Sid    = "ECRPull"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
        ]
        Resource = [for r in aws_ecr_repository.images : r.arn]
      },
      {
        Sid    = "CloudWatchLogsAccess"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogStreams",
        ]
        Resource = "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:${local.names.log_group_sm}*"
      },
      {
        Sid      = "CloudWatchMetrics"
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
      },
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "sagemaker_exec" {
  role       = aws_iam_role.sagemaker_exec.name
  policy_arn = aws_iam_policy.sagemaker_exec.arn
}

# ----- Model Registry の器 -----
#
# グループ（器）は Terraform で作れるが、版の登録と承認は SDK 側に残る。

resource "aws_sagemaker_model_package_group" "main" {
  model_package_group_name        = local.names.model_package
  model_package_group_description = "California Housing regressor versions (Tier A / SageMaker AI)"

  tags = local.common_tags
}

# ----- Model -> Endpoint Config -> Endpoint（学習後の 2 段階目） -----
#
# Vertex は Endpoint の器を artifact 非依存で先に作れるが、SageMaker は作れない。
# model_data_url / serving_image_uri が空の間はこの 3 リソースを作らない。

resource "aws_sagemaker_model" "main" {
  count = local.deploy_enabled ? 1 : 0

  name               = "${local.names.model}-${substr(sha1(var.model_data_url), 0, 8)}"
  execution_role_arn = aws_iam_role.sagemaker_exec.arn

  primary_container {
    image          = var.serving_image_uri
    model_data_url = var.model_data_url
  }

  tags = local.common_tags

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_sagemaker_endpoint_configuration" "main" {
  count = local.deploy_enabled ? 1 : 0

  name = "${local.names.endpoint_config}-${substr(sha1(var.model_data_url), 0, 8)}"

  production_variants {
    variant_name           = "AllTraffic"
    model_name             = aws_sagemaker_model.main[0].name
    initial_instance_count = var.endpoint_instance_count
    instance_type          = var.endpoint_instance_type
  }

  tags = local.common_tags

  lifecycle {
    create_before_destroy = true
  }
}

# 常時課金。フェーズ末に必ず destroy し、残留は check_residual.py で確認する。
resource "aws_sagemaker_endpoint" "main" {
  count = local.deploy_enabled ? 1 : 0

  name                 = local.names.endpoint
  endpoint_config_name = aws_sagemaker_endpoint_configuration.main[0].name

  tags = local.common_tags
}

# ----- 予算アラート -----
#
# Tier A は各 ¥2,000/月（docs/01_requirements.md）。AWS Budgets は JPY 指定不可のため USD で設定する。
# GCP（project 単位でフィルタ）と違い、アカウント全体が対象（タグ絞り込みはコスト配分タグの有効化が前提）。

resource "aws_budgets_budget" "monthly_guardrail" {
  count = var.budget_notification_email == "" ? 0 : 1

  name         = local.names.budget
  budget_type  = "COST"
  limit_amount = tostring(var.budget_amount_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 50
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_notification_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 90
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_notification_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_notification_email]
  }
}
