# state のリモート化。local state のままだと destroy 漏れの追跡ができない。
#
# **Tier B は自前で state を置けない。** Databricks の UC Volume も Snowflake の
# stage も Terraform backend として使えず（2026-08-01 実測）、データは基盤の中に
# あってオブジェクトストレージが外に無い。この非対称そのものが比較材料。
#
# 以前は GCP の state バケットへ相乗りしていたが、**GCP を畳むと
# Tier B 2基盤の state を失う**（「基盤を独立に畳める」前提が崩れる）。
# 計測用に既にある Neon を中立の置き場として使う —— Neon は比較対象の5基盤に
# 含まれないので、どの基盤を畳んでも state は生きる。
#
# ⚠️ **direct endpoint を使うこと。** pg backend は state lock に
# セッションレベル advisory lock を使い、pooled（transaction mode）では動かない
# （docs/02_architecture.md「transaction mode で使えないもの」）。
#
# ⚠️ **conn_str に `options=endpoint=<id>` が要る。** pg backend の lib/pq は
# SNI 非対応で、素の Neon URI では
# `Endpoint ID is not specified` で init が落ちる（2026-08-01 実測）。
# 組み立ては scripts/tf_backend.py が行う。
#
# 初期化:
#   doppler run -- python scripts/tf_backend.py sf-dev | xargs terraform -chdir=infra/environments/sf-dev init

terraform {
  backend "pg" {
    schema_name = "tfstate_sf_dev"
  }
}
