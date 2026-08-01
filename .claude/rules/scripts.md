---
paths:
  - "scripts/**/*.sh"
  - "**/*.sh"
---

# シェルスクリプト / ops adapter ルール

- 冒頭に `set -euo pipefail`。`bash -n <file>` で構文チェックしてから確定する。
- 運用系は薄い adapter に保つ：`scripts/ops-*.sh` にパス解決・secret 取得・binary チェック・status / kick / stop / dedupe・短い stdout 要約・正しい exit code を置く。判断 / スケジュール / 通知 / retry は上位（n8n 等）に寄せる。
- **status が duplicate を報告している間は kick しない**。先に dedupe か手動調査。
- secret 値・token・cookie・個人パスを echo / log しない。値は secret manager か gitignore 済みローカルから読む。
- 破壊的操作（process 停止・cron/plist 編集・ファイル移動/削除）は dry-run / plan-first を既定にし、Heavy として扱う。
- 終了コードで結果を表す（成功 0 / 検出あり 1 / 実行不能 2 等）。exit code を握りつぶさない。
