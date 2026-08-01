# Snowflake Phase 5 着手前確認

> ✅ **消化済み（2026-08-01）**。該当 Phase は完走し、実測は `docs/comparison/` が正本。
> このファイルは着手前に何を潰したかの記録として残す。

Weight Class: Light（調査のみ）

## Goal

Phase 5（Snowflake）開始前に、現行ドキュメントで次の3点を確認する。

1. トライアルのクレジット額と有効期限（**実質のタイムボックスになる**）
2. 外部ネットワークアクセス（Neon への到達）に必要なオブジェクトと権限一式（network rule / secret / external access integration の構成と権限）
3. Model Registry の Terraform 対応範囲
4. Terraform プロバイダの現行状態: source 名リネームへの versions.tf 追随、**プレビュー機能の既定無効**（`preview_features_enabled` へ機能名を明示追加。メジャー版内でも破壊的変更あり）。**External Access Integration がプレビュー扱いかを `MIGRATION_GUIDE.md` で確認**
5. **Snowflake Anaconda channel の lightgbm バージョン**（本リポジトリは `>=4.6,<4.7` 固定。2026-08-01 に `SPROC_PACKAGES` へこの版指定を明記済み = channel に無ければ `CREATE PROCEDURE` が落ちて即分かる）。あわせて **PACKAGES 句が範囲指定子（`>=,<`）を受理するか**を確認する（公式例は `pkg==x.y.z` の完全一致のみ。受けなければ channel の実提供版へ `==` で書き換え、pyproject と同 minor であることを test_snowflake_adapter の pin で確認）。同一 minor が無い場合は metric parity の許容幅を広げるのではなく、**channel にある minor へ5基盤全体を揃え直す**（owner 承認 2026-07-31: Phase 5 着手時に確認）。psycopg3 は channel に無い前提で設計済み（telemetry は JSONL fallback + `make collect`）

## Value

トライアル期限が Phase 5 の実行可能期間を直接決める。Phase 5 は他フェーズのような分散実行が効かず「まとめて一気にやる」前提で計画する必要があり、開始前にタイムボックスを確定しないと完走できない。

## Scope

- 上記3点の現行ドキュメント確認と記録
- Phase 5 の一括実行計画（タイムボックス）への反映

## Non-scope

- Snowflake インフラ・adapter の実装
- SPCS の評価（主経路にしない。差分メモ止まり）

## 実装時の既知の罠

- **`Snowflake-Labs/sfguide-snowpark-scikit-learn` の California Housing は Kaggle/handson-ml 版**（OCEAN_PROXIMITY 等・One-Hot あり）で、本プロジェクトの `fetch_california_housing` とは列も目的変数スケールも別物。**配管（sproc 登録・stage・Model Registry・UDF 推論）のみ流用し、データ層は必ず差し替える**。見落とすと Snowflake だけ RMSE 不一致になり原因究明で溶ける（[reuse-asset-import-map.md](./reuse-asset-import-map.md) A-5）

## Done

- 3点の確認結果（参照ドキュメントの日付付き）が本 task に記録され、Phase 5 の一括実行計画に反映されている

## Evidence

- Snowflake 公式ドキュメント / Terraform provider ドキュメント（参照日明記）

## Stop / Ask Owner If

- **トリガ**: SnowPro Core 着手時（= Phase 5 開始判断時）に実施。それまで着手不要。

## 出典

- [../../archive/managed-ml-platform-comparison-brainstorm-v2.md](../../archive/managed-ml-platform-comparison-brainstorm-v2.md) §6・§13
