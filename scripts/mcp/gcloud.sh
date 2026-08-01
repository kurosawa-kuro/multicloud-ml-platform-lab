#!/usr/bin/env bash
# gcloud MCP サーバ起動ラッパー（@google-cloud/gcloud-mcp・googleapis 公式・Apache-2.0）。
#
# このスクリプトが要る理由:
#   allowlist/denylist の設定ファイルは **絶対パス** でしか渡せない
#   （https://github.com/googleapis/gcloud-mcp/blob/main/doc/denylist.md）。
#   登録側（ユーザースコープ / .mcp.json）にマシン依存の絶対パスを書かずに済むよう、
#   パス解決をここへ寄せる（scripts/mcp/neon.sh と同じ役割）。
#
# 認証: gcloud の ADC をそのまま使う。
#   gcloud auth application-default login  /  gcloud config set project <id>
#
# 権限: provider 側の既定 denylist（変更不可）に加えて
#   scripts/mcp/gcloud-policy.json で「削除・課金・鍵作成・シークレット読み出し・
#   トークン印字」を拒否する。terraform destroy 系は Terraform 側の owner 承認境界に従う。
set -euo pipefail

POLICY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/gcloud-policy.json"

if [ ! -f "$POLICY" ]; then
  echo "mcp/gcloud: ポリシーファイルが見つかりません: $POLICY" >&2
  exit 2
fi

exec npx -y @google-cloud/gcloud-mcp@0.5.3 -c "$POLICY"
