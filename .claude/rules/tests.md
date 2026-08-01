---
paths:
  - "**/tests/**"
  - "**/*_test.*"
  - "**/test_*.*"
  - "**/*.test.*"
  - "**/*.spec.*"
---

# テストルール

- テストは**仕様の写像**。期待値を実装都合に合わせて弱めない（`plan-skeleton` のテスト接続修正は import/DI/mock/fixture を現実構造に合わせる作業であり、期待値の緩和ではない）。
- 挙動を変えたら、まず失敗するテスト（RED）を先に足し、実装で GREEN にする。happy-path が緑なだけで done にしない。
- 外部副作用（ネットワーク・課金 API・本番 DB・通知）はモック / fixture で隔離する。実接続テストは明示マークで分け、既定の `make test` から外す。
- flaky を放置しない。非決定要因（時刻・乱数・順序・並行）は注入で固定する。
- done の下限は Evidence Level ≥2（実テスト / 実行時 / 実 DB の観測）。docs の緑チェックや主張文を証拠にしない（`docs/specs/evidence-policy.md`）。
