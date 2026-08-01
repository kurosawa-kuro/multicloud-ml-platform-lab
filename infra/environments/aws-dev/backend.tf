# state のリモート化。local state のままだと destroy 漏れの追跡ができない。
#
# 各クラウドのオブジェクトストレージへ置く（gcs / s3 / azurerm）。
# 基盤ごとに backend が異なること自体が比較材料になるので、
# どれを使ったかを docs/comparison/ に記録する。
#
# GCP 側（gs://<project-id>-tfstate 直書き）と違い、AWS 側には state 用バケットがまだ無い。
# 実在しない名前を固定すると init が必ず落ちるため partial config にしてある:
#
#   aws s3api create-bucket --bucket <state-bucket> --region ap-northeast-1 \
#     --create-bucket-configuration LocationConstraint=ap-northeast-1
#   terraform init -backend-config="bucket=<state-bucket>"
#
# state ロック（DynamoDB テーブル / use_lockfile）は未設定。CI から回す前に決める。

terraform {
  backend "s3" {
    key     = "multicloud-ml-platform-lab/aws-dev/terraform.tfstate"
    region  = "ap-northeast-1"
    encrypt = true
  }
}
