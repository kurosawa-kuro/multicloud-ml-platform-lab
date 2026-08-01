# 07 テスト戦略

> `make test`（pytest）は実装済みで、ユニット / contract / parity（合成データ分）はクラウド資格情報なしで走る。範囲表のうち **smoke（実基盤ストレージ疎通）と live E2E は未実装**（各 Phase で追加）。

## 品質ゲート

```bash
make test        # pytest（tests/ 全件）
git diff --check
```

## テスト範囲

| 範囲 | 対象（予定） | 備考 |
|---|---|---|
| ユニット | `src/core/ml` の純関数（load/validate/split/metrics）、telemetry の分類ロジック | クラウド資格情報なしで走る。SDK を import しない層に限定 |
| contract | `test_code_revision_parity.py`（全基盤 run の同一SHA）、destroy 順序・保護対象のソース pin、Makefile ターゲット存在 pin、Terraform module 構成 pin | 「正本1箇所 + 参照側を全部 pin」の型（gke 由来） |
| parity | `test_metric_parity.py`（同一SHA・同一 seed で**全基盤の RMSE 一致**） | 期待値の基準線: ローカル基準実装の実測（参考: kaggle-bronze の CH ベースライン RMSE 0.44498） |
| smoke | 各基盤ストレージの write→read→一致、serving 契約ルートの応答（/health, /predict, /ping, /invocations, /score） | 5〜10秒で終わる最小疎通。`--strict` で CI ゲート化 |
| live E2E | Phase 完了条件8項目の1周（= Golden Path） | terminal state（レポート1列が実測で埋まる）まで通して初めて緑 |

## 検証の原則

- **クラウド検証をローカル fake で代替しない**。実基盤（または本番同型の使い捨てリソース）で該当 SQL / API 経路を実際に走らせる。fake の緑は本番の赤。
- **false PASS を禁じる**: 検証は反証可能に書く（何が観測されたら FAIL かを先に決める）。happy-state の確認だけで緑にしない。
- **中間段の緑で完了にしない**: ビルド緑・ヘルスチェック 200 ではなく、terminal artifact（Neon の行・レポートの実測値）を観測して完了とする。
- 型が変わる移植・migration は、1件見つけた同種の問題を機械的に全数 census する（1件直して緑扱いにしない）。

## タスク完了条件

各 task は、完了前に必要な検証を `Verification` に残す。

- 実行したコマンド
- 結果の要約
- 実行できなかった検証と理由
- 残るリスク

テスト追加や期待値変更が必要な場合は、task に理由を書き、仕様変更なら先に該当 docs を更新する。
