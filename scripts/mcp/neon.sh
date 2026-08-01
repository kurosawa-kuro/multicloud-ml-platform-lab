#!/usr/bin/env bash
# Neon MCP サーバ起動ラッパー。
#
# @neondatabase/mcp-server-neon は API キーを「コマンドライン引数」でしか受け取らない
# （NEON_API_KEY 環境変数は非対応）。一方 .mcp.json はコミット対象で秘密を書けず、
# args 内の ${VAR} も展開されない。そこでキーの解決だけをこのスクリプトに寄せる。
#
# 解決順:
#   1. 既に環境変数 NEON_API_KEY がある（`doppler run -- claude` で起動した場合）
#   2. 無ければ Doppler CLI から直接取得（素の `claude` で起動した場合）
set -euo pipefail

KEY="${NEON_API_KEY:-}"

if [ -z "$KEY" ]; then
  KEY="$(doppler secrets get NEON_API_KEY --plain --project cloud-ml-lab --config dev)"
fi

if [ -z "$KEY" ]; then
  echo "mcp-neon: NEON_API_KEY を解決できません（doppler login 済みか確認）" >&2
  exit 1
fi

exec npx -y @neondatabase/mcp-server-neon start "$KEY"
