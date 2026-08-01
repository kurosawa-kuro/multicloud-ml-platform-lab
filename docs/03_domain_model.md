# 03 ドメインモデル

> 出典: [01_requirements.md](./01_requirements.md) / [02_architecture.md](./02_architecture.md) から用語を集約。定義の重複を避けるため、要件・制約の本文は 01 を、構成・境界は 02 を正とする。

## 用語

| 用語 | 意味 |
|---|---|
| platform（基盤） | 比較対象の5つ: `vertex` / `sagemaker` / `azureml` / `databricks` / `snowflake` |
| Tier | 基盤の2階層。**Tier A** = コンテナ実行型（Vertex/SageMaker/Azure ML）、**Tier B** = データ基盤内蔵型（Databricks/Snowflake） |
| 統一単位（unification unit） | 5基盤を「同一」とみなす根拠。全体では **`src/core/ml` の同一 git SHA**。配布形態は Tier A = `container`（BYOC）、Tier B = `package`（wheel / stage upload） |
| fixture（固定具） | California Housing データセット。題材ではなく実験の固定具。**sklearn 版（`fetch_california_housing`）のみを正とする**（Kaggle/handson-ml 版は列も目的変数スケールも別物・混入禁止） |
| run | 1回の実行記録（`ml_runs` の1行）。platform × stage × attempt で識別し、成功も失敗も等しく記録する |
| stage | run の種別: `train` / `register` / `deploy` / `predict` |
| attempt | 同一目的の試行回数。「最小権限で通るまでに何回直したか」の分子 |
| failure_class | 失敗の分類: `iam` / `quota` / `container` / `package` / `network` / `sdk` / `data` / `none`（詳細は [06_error_policy.md](./06_error_policy.md)） |
| code_revision | `src/core/ml` の git SHA。**比較成立の唯一の担保**（全基盤で一致必須） |
| write_path | 計測データが Neon へ到達した経路: `direct`（ジョブ内から直接 INSERT）/ `collected`（JSONL fallback → `make collect`） |
| 計測到達点 | Neon PostgreSQL。全基盤の run / infra_event / cost が集約され、SELECT だけで比較できる状態が到達点。到達可否・経路の重さ自体が比較軸 |
| infra_event | terraform apply / destroy 1回の記録（所要・リソース数・残留） |
| 残留リソース（residual） | destroy 後もクラウド側に残るもの。`check-residual` で列挙し `infra_events.residual_resources` に記録 |
| Phase | 実施順序の単位: 0 ローカル基準 → 1 Vertex → 2 SageMaker → 3 Databricks → 4 Azure ML（go/no-go）→ 5 Snowflake（一気に完走） |
| precheck | 各 Phase 着手前の現行ドキュメント確認タスク（`docs/tasks/02_backlog/*-check.md` / `*-precheck.md`） |
| Golden Path | 1基盤分のフェーズを apply → 学習 → Neon 到達 → 登録/推論 → destroy → 残留記録 → レポート記述まで通す一本道（[01_requirements.md](./01_requirements.md)） |
| 比較レポート | `docs/comparison/` 配下の主成果物（method 宣言 + 基盤別1ページ + 選定チェックリスト + 残留比較表） |
| metric parity | 同一SHA・同一 seed で全基盤の RMSE が一致すること。不一致は基盤差ではなく実装漏れ |

## 状態 / ライフサイクル

run（`ml_runs.status`）:

```text
(投入) ──成功──> success
   └────失敗──> failure   ※ failure_class 付きで必ず記録し、attempt を上げて再試行
```

Phase（完了条件8項目 = [01_requirements.md](./01_requirements.md) の制約）:

```text
precheck 完了
  -> ① terraform apply          (infra_event: apply)
  -> ② 学習ジョブ成功            (ml_runs: train / 失敗も記録)
  -> ③ Neon へメトリクス到達     (write_path 記録)
  -> ④ モデル登録                (ml_runs: register)
  -> ⑤ 1件オンライン推論         (ml_runs: deploy, predict)
  -> ⑥ terraform destroy        (infra_event: destroy)
  -> ⑦ 残留リソース記録          (residual_resources)
  -> ⑧ 比較レポート1ページ記述   ← 次 Phase のブロック条件
```

## 関連タスク

- 用語、状態、ライフサイクルの変更は task に背景と影響範囲を残してから反映する。
- 未確定の業務ルールはこの文書へ入れず、`docs/tasks/02_backlog/` で調査対象として管理する。
- 確定したドメイン判断は、必要なら `docs/decisions/` にも残す。
