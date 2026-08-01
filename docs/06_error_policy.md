# 06 エラー方針

> 原則: **失敗は一級データ**。握り潰さず・自動で success に倒さず、必ず `ml_runs` に failure_class 付きで記録する。「最小権限で通るまでに何回直したか」がこのプロジェクトの主成果物の材料になる。

## エラー分類（`ml_runs.failure_class`）

| 分類 | 意味 | 対応 |
|---|---|---|
| `iam` | 権限不足・ロール/ポリシー不備 | 最小権限のまま1つずつ直し、**修正1回ごとに attempt を上げて記録**（まとめて広い権限を付けない） |
| `quota` | クォータ・上限超過 | 引き上げ申請 or リージョン/マシンタイプ変更。記録して次へ |
| `container` | イメージ・entrypoint・コンテナ契約の不備（Tier A） | シム/Dockerfile を修正。契約差そのものが比較材料 |
| `package` | 依存パッケージ起因（Tier B: Anaconda channel 制約・ML Runtime 衝突） | `src/core/ml` の依存を削って再統一。削れなければ「依存制約」を発見として記録 |
| `network` | 外部到達不能（Neon への egress / external access integration） | **fallback（JSONL → `make collect`）へ切替**し、到達不能自体を結果として記録 |
| `sdk` | クラウド SDK のエラー・API 変更 | precheck（現行ドキュメント確認）へ戻る。推測で回避しない |
| `data` | 入力データ不備 | fail-fast 検証（validate）で早期検出。**Kaggle 版 CH 混入はここで止める** |
| `none` | 成功 | — |

## 実装上の原則

- **telemetry は非致命**: 計測記録の失敗で学習本体を止めない（Err だけでなく panic/例外も事前ガード）。ただし記録失敗の事実は warning としてローカル artifact に残す。
- **学習とトランザクションを共有しない**: 学習失敗時にも記録が残る必要がある。接続は開いて即閉じる。
- **Neon cold start**: suspend 明けの初回接続は遅い。初回接続は最大3回リトライ（間隔2s）。**タイムアウト上限の引き上げで対処しない**（症状の隠蔽）。
- **RMSE 不一致は即停止**: 基盤の差ではなく実装漏れ。原因判明まで次の Phase へ進まない。
- **partial failure を握り潰さない**: 複数基盤・複数 stage の一括処理では、1件の失敗を全体成功に丸めない。
- **リトライは分類してから**: failure_class が `iam` / `container` / `package` / `data` のものは自動リトライしない（同じ失敗を重ねるだけ）。リトライ対象は一過性（`network` の接続断・cold start）のみ。

## ログ

- 秘密情報をログに出さない。**接続 URL・パスワードは出さず host のみ表示**（private-ops `pg.py` の流儀）。
- `ml_runs.error_excerpt` にも秘密情報・トークンを入れない（エラーメッセージの貼り付け時に注意）。
- タイムスタンプは UTC。

## 関連タスク

- エラー分類、リトライ、ログ出力の変更は task に再現条件と期待する観測結果を残す。
- 障害対応で得た恒久手順は task に閉じず、`docs/runbooks/` へ昇格する。
- 回帰防止が必要なものは `07_test_strategy.md` と task の `Acceptance Criteria` に反映する。
