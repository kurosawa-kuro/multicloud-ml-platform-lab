# state のリモート化。local state のままだと destroy 漏れの追跡ができない。
#
# 各クラウドのオブジェクトストレージへ置く（gcs / s3 / azurerm）。
# 基盤ごとに backend が異なること自体が比較材料になるので、
# どれを使ったかを docs/comparison/ に記録する。
#
# このバケットは Terraform 管理外（chicken-and-egg）。事前に作成しておくこと:
#   gsutil mb -l us-central1 gs://<project-id>-tfstate
#
# **bucket をここに直書きしない**（aws-dev / azure-dev と同じ partial config）。理由は2つ:
#   1. 実プロジェクト ID が repo に焼かれる。state バケット名の露出は、中身が tfstate
#      （機微な値を含む）である分、ふつうのバケットより重い。
#   2. GCP だけ直書きだと「識別子の正本は Doppler」という他4環境との対称性が崩れる。
#
# init は bucket を明示して行う（値は Doppler の GOOGLE_CLOUD_PROJECT から導出）:
#
#   doppler run -- sh -c 'terraform -chdir=infra/environments/gcp-dev init \
#     -backend-config="bucket=$GOOGLE_CLOUD_PROJECT-tfstate"'
#
# 既に init 済みの作業ツリーで切り替えるときは `-reconfigure` を足す。

terraform {
  backend "gcs" {
    prefix = "multicloud-ml-platform-lab/gcp-dev"
  }
}
