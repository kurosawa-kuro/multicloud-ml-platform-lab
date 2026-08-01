# state のリモート化。local state のままだと destroy 漏れの追跡ができない。
#
# 各クラウドのオブジェクトストレージへ置く（gcs / s3 / azurerm）。
# 基盤ごとに backend が異なること自体が比較材料になるので、
# どれを使ったかを docs/comparison/ に記録する。
#
# GCP 側（gs://<project-id>-tfstate 直書き）と違い、Azure 側には state 用の
# Storage Account がまだ無い。実在しない名前を固定すると init が必ず落ちるため
# partial config にしてある（AWS 側と同じ扱い）:
#
#   terraform init \
#     -backend-config="resource_group_name=<rg>" \
#     -backend-config="storage_account_name=<sa>" \
#     -backend-config="container_name=tfstate"
#
# state ロックは azurerm backend が blob lease で行うため追加リソース不要
# （S3 backend の DynamoDB に相当するものが要らない = 比較材料）。

terraform {
  backend "azurerm" {
    key = "multicloud-ml-platform-lab/azure-dev.tfstate"
  }
}
