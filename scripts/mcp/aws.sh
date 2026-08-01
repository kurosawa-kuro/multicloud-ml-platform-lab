#!/usr/bin/env bash
# AWS MCP（マネージド remote）への接続ラッパー。
#
# このスクリプトが要る理由:
#   mcp-proxy-for-aws はローカルの AWS credential chain を使う。資格情報が
#   1つも解決できないと remote 側が initialize を -32602 で弾き、MCP 接続自体が失敗する。
#   この端末の AWS 資格情報は Doppler にしか無いため、素の `claude` 起動でも
#   繋がるようにキー解決をここへ寄せる（scripts/mcp/neon.sh と同じ役割）。
#
# 解決順:
#   1. 既に AWS_ACCESS_KEY_ID がある（`doppler run -- claude` で起動した場合）
#   2. 無ければ Doppler 経由で起動する
#
# --read-only を外すと aws___call_aws / aws___run_script が有効になるが、
# 現在の資格情報は root アクセスキーなので既定では付けたままにする
# （docs/tasks/02_backlog/aws-root-key-最小権限化.md が完了したら外す）。
set -euo pipefail

PROXY_ARGS=(
  mcp-proxy-for-aws@1.6.4
  https://aws-mcp.us-east-1.api.aws/mcp
  --metadata AWS_REGION=ap-northeast-1
  --read-only
)

if [ -n "${AWS_ACCESS_KEY_ID:-}" ]; then
  exec uvx "${PROXY_ARGS[@]}"
fi

exec doppler run --project cloud-ml-lab --config dev -- uvx "${PROXY_ARGS[@]}"
