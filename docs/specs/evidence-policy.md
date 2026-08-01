# Evidence Policy（このプロジェクトの instantiation）

**Layer 8: Evidence / Reality Check**（§16）の repo 固有 instantiation。汎用 Evidence Level を*このリポジトリ*の確実な成果へ写像する。Skill: `verify-completion`（+ `check-claims`）。reminder hook: `detect-unverified-claim`。

> **使い方（厚いメニュー → 削る）**: Level 定義は汎用なのでそのまま。下の「確実な成果」メニューは作業種別ごとに厚く列挙してある。**このプロジェクトに無い種別の行は削る**。

## 中核ルール

> エージェントの「完了しました」は証拠ではない。証拠とは diff / test 出力 / コマンドログ / 実ファイル / 実行時の挙動 / 永続化された成果である。
> **done は Evidence Level ≥2 が下限（docs-only タスクを除く）。本番影響のある変更は Level 4 + owner approval。**

## Evidence Level（汎用・そのまま）

| Level | 名前 | これで証明される |
|---:|---|---|
| 0 | Claim Only | 「動くはず」 — 受理しない |
| 1 | Static | diff、実ファイル内容、`cargo check` / 型チェック / lint — docs-only や trivial 編集 |
| 2 | Test | `make test` 出力、修正前fail→修正後pass — **標準の下限** |
| 3 | Runtime | ローカル実行＋永続化された成果（下表のいずれか） |
| 4 | Production | 本番ログ / 監視チェック / ロールバック準備（`docs/08_release_runbook.md`）— owner approval 必須 |

## 確実な成果メニュー（作業種別ごと。最低1つ確認。無い種別は削る）

| 作業種別 | 確実な成果（durable outcome）の例 |
|---|---|
| API / サーバ | リクエストを投げ期待 status/body、エラー時の挙動、ログ行を確認 |
| DB 書込/集計 | 対象行が実際に増減・重複なし・制約違反なし（`SELECT` で確認） |
| migration | up→down→up が通る・既存データ保持・スキーマ反映を確認 |
| バッチ/パイプライン | 入力件数→出力件数が一致・冪等（再実行で二重生成なし） |
| 通知/外部送信 | 送信記録/ログ・二重送信なし（dry-run と件数一致） |
| AI/LLM 呼び出し | 結果が model/prompt version/status つきで保存・コスト把握 |
| cleanup/削除 | 削除件数が事前 dry-run と一致・対象外を消していない |
| CLI/ツール | 実際に実行し exit code・stdout・生成物を確認 |
| frontend | 実際に描画/操作し期待 UI・コンソールエラーなし |
| 設定/IaC | plan の差分を確認・apply 後に状態を再取得して一致 |
| docs | 正本 doc を同一変更で更新・リンク切れなし |

## ルール

- すべての PASS に `file:line` かコマンド出力を引用する。未証明は UNVERIFIED であり PASS にしない。
- 確実な成果チェックの無い「緑のワークフロー」は最大でも Level 1 で、done ではない。
- 本番検証は runbook（`docs/08_release_runbook.md`）であって、ビルド成功ではない。
