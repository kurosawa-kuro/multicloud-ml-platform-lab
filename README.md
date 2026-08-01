# multicloud-ml-platform-lab

同一のデータセット（California Housing）と同一SHAの学習コードを、5つのマネージドML基盤
（**Vertex AI / SageMaker AI / Azure ML / Databricks / Snowflake**）で学習・登録・推論・撤退まで実行し、
**選定時に効く差分を実測して文書化する**比較ラボ。

精度比較ではなく、次を測る:

- 権限設計に要した試行回数（最小権限で学習ジョブが通るまで何回直したか）
- IaC（Terraform）で管理できる境界 / SDK・SQL に残る境界
- 外部DB（Neon PostgreSQL）への到達経路と設定の重さ
- 撤退後の残留リソース（destroy しても消えないもの）
- アイドル時課金の構造差

主成果物はコードではなく **比較レポート＋選定チェックリスト**（[`docs/comparison/selection-checklist.md`](docs/comparison/selection-checklist.md)）。`src/` は計測装置。

**作らないもの（意図的な除外）**: Feature Store（5基盤すべて不使用）・ドリフト検知・モデル監視・Pipelines 比較・機能比較マトリクス（[thoughtworks/mlops-platforms](https://github.com/thoughtworks/mlops-platforms) の劣化コピーになる）・データ近接の性能比較（2万行では測れない）。理由は [docs/01_requirements.md](docs/01_requirements.md) の非対象を参照。何を作らなかったかは、作ったものと同じだけ情報量がある。

> **状態（2026-08-01）**: **5基盤すべて実測完了**（Phase 1〜5 とも完了条件 8/8）。
> 結論は [選定チェックリスト](docs/comparison/selection-checklist.md)、
> 撤退後の残留は [residual-resources.md](docs/comparison/residual-resources.md)、
> 基盤別の一次記録は [`docs/comparison/01`〜`05`](docs/comparison/)。

## 実測結果（2026-08-01）

**モデルの出力は5基盤で完全に一致した。**

| 指標 | 5基盤の値 |
|---|---|
| RMSE | **0.4368055090296257**（ローカル基準値とも一致） |
| 1件推論の予測値 | **4.183217948107466** |

**だから差が出たのは「そこへ到達するまでの経路」だけ**で、それがこのラボの成果物になる。

| | Vertex AI | SageMaker | Databricks | Azure ML | Snowflake |
|---|---|---|---|---|---|
| Tier / 統一単位 | A / イメージ | A / イメージ | B / wheel | A / イメージ | B / zip |
| Terraform リソース数 | 17 | 15 | 7 | 10 | 11 |
| apply 試行回数 | **1** | **1** | 2 | 3 | 4 |
| destroy 試行回数 | **1** | 2 | 2 | 3 + 手動 | **1** |
| 権限の追加回数 | 0 | 0 | 0 | **1** | 0 |
| Neon 到達経路 | direct | direct | collected | direct | collected |
| 残留リソース | WARN 1 | WARN 3 | **0** | IaC 管理外 1 | WARN 2 |

主な発見は3つ。

1. **「できない」の正体が技術ではなく契約だった**（Snowflake の External Access Integration、
   Azure の Spot 枠）。どちらも Terraform の外側にあり、コードの修正では越えられない。
2. **Tier A は3基盤とも `direct`、Tier B は2基盤とも `collected`** で仮説どおり。
   ただし collected に落ちた理由は基盤ごとに違う。
3. **課金が続く残留（FAIL）は5基盤ともゼロ。** 残留の怖さは課金ではなく
   「再作成を塞ぐこと」と「撤退に手作業が要ること」に移る。

## 技術スタック

| レイヤー | 技術 |
|---------|------|
| 言語 | Python 3.12（依存最小: lightgbm / scikit-learn / pandas / pyarrow） |
| モデル | LightGBM Regressor（baseline: RandomForest）・seed 固定・RMSE/MAE/R2 |
| 推論（Tier A） | FastAPI 1アプリで3基盤の契約（/health,/predict,/ping,/invocations,/score） |
| データベース | Neon PostgreSQL（計測データ集約: ml_runs / infra_events / cost_snapshots） |
| インフラ | Terraform（静的基盤のみ。ジョブ実行は各 SDK/CLI/SQL） |
| 対象基盤 | Tier A: Vertex AI / SageMaker / Azure ML（BYOC 統一）・Tier B: Databricks / Snowflake（wheel / stage） |

## セットアップ

```bash
make setup    # venv + 依存（uv）
make test     # pytest（クラウド資格情報なしで走る）
```

設定は `env/config.yaml`（非機密）、`env/secret.yaml`（ローカル秘密情報）、Doppler（共有・本番秘密情報）で管理する。
`env/secret.yaml` は `.gitignore` で除外されるためコミットしない。

## ディレクトリ構成

```
.
├── env/                  # config.yaml（非機密）/ secret.yaml（コミット禁止）
├── src/                  # 計測装置（core/{ml,app,telemetry} + platforms/ 基盤別 adapter・Neon 層）
├── docs/                 # source-of-truth ドキュメント + comparison/（主成果物）
├── infra/                # Terraform modules / environments（5基盤 + neon）
├── docker/               # Tier A 学習コンテナ + entrypoint シム
├── sql/                  # schema.sql / comparison_queries.sql
├── scripts/              # check_residual / collect_costs / collect_jsonl / mcp
└── .claude/rules/        # Claude Code パス別作業ルール
```

## ドキュメント

開発・運用の詳細は [`docs/00_index.md`](docs/00_index.md) を参照。

- [`docs/01_requirements.md`](docs/01_requirements.md) — 目的、範囲、制約、Golden Path
- [`docs/02_architecture.md`](docs/02_architecture.md) — 2階層構造、実行契約、Neon 集約、Terraform 境界
- [`docs/04_workflows.md`](docs/04_workflows.md) — フェーズ実行ワークフロー
- [`docs/07_test_strategy.md`](docs/07_test_strategy.md) — parity / contract テスト方針
- [`docs/comparison/selection-checklist.md`](docs/comparison/selection-checklist.md) — **主成果物**。5基盤の判断軸を実測で埋めた選定表
- [`docs/comparison/residual-resources.md`](docs/comparison/residual-resources.md) — 撤退後に何が残ったか（5基盤横断）
- [`docs/runbooks/`](docs/runbooks/README.md) — 基盤ごとの実手順と合否判定（再実行するときの入口）
- 既存資産・外部OSSの流用元と移植時に変えた点は、各 adapter のモジュール docstring に記録
