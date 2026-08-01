---
paths:
  - "**/*"
---

# セキュリティルール

- 認証情報・API キー・接続文字列・秘密鍵・webhook・cookie・個人データを絶対にコミットしない。
- ローカル秘密情報は ignore するローカルファイルに置く。
- 共有・本番の秘密情報は Doppler などの secret manager に置く。
