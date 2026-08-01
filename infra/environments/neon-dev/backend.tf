# state のリモート化。local state のままだと destroy 漏れの追跡ができない。
#
# 各クラウドのオブジェクトストレージへ置く（gcs / s3 / azurerm）。
# 基盤ごとに backend が異なること自体が比較材料になるので、
# どれを使ったかを docs/comparison/ に記録する。

terraform {
  # TODO(Phase): backend 設定
}
