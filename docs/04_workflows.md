# 04 ワークフロー

> `Makefile` のターゲットはローカル系・フェーズ実行系とも実装済み。**5基盤すべて完走済み（2026-08-01）**で、結論は [comparison/selection-checklist.md](./comparison/selection-checklist.md)。着手時点のギャップ一覧は 仕様準拠監査-2026-08-01.md（クローズ済み）。
>
> **本文書は5基盤共通の骨格**（make ターゲットの並び）。基盤ごとに違う実手順と合否判定は [runbooks/](./runbooks/README.md) の動作検証 runbook を見る。配布物・入出力・deploy の有無・推論 payload・残留はすべて基盤ごとに違うので、ここでは共通化しない。

## セットアップ

```bash
make help
make setup    # venv + 依存（uv。pyproject.toml が正本）
```

## 作業開始

```bash
git status --short
```

1. [tasks/README.md](./tasks/README.md) を見る。
2. `docs/tasks/01_active/` から今日の task を選ぶ（Phase 着手時はまず `02_backlog/` の precheck を消化する）。
3. task に Scope / Plan / Acceptance Criteria があることを確認する。
4. 中規模以上なら Skeleton を固定してから実装する。

## フェーズ実行ワークフロー

各 Phase は完了条件8項目（[01_requirements.md](./01_requirements.md)）をそのまま make ターゲット列にする。Makefile はロジックを持たず Python CLI（`scripts/run_terraform.py` / `scripts/run_phase.py`）へ委譲する。

対象の指定が2種類あるので混同しない。**`ENV` = terraform 環境**（`gcp-dev` / `aws-dev` / `azure-dev` / `dbx-dev` / `sf-dev`）、**`PLATFORM` = 比較対象の基盤**（`vertex` / `sagemaker` / `azureml` / `databricks` / `snowflake`）。

```bash
# 例: Phase 1 (Vertex) の1周
make ENV=gcp-dev tf-plan                    # 差分を人が読む（plan-first）
make ENV=gcp-dev tf-apply                   # ① apply（所要・リソース数を infra_events へ）
make PLATFORM=vertex phase-train            # ② 学習ジョブ投入（失敗も ml_runs に記録）
                                            # ③ Neon 到達はジョブ内で記録（不達なら JSONL fallback）
make collect                                # ③' fallback 分をローカルから Neon へ流し込み
make PLATFORM=vertex phase-register         # ④ モデル登録
make PLATFORM=vertex phase-deploy           # ⑤a デプロイ（⚠️ 常時課金）
make PLATFORM=vertex phase-predict          # ⑤b 1件オンライン推論
make PLATFORM=vertex phase-teardown         # エンドポイントを落とす（destroy の前に）
make ENV=gcp-dev tf-destroy                 # ⑥ destroy → ⑦ 残留検査まで連鎖
# ⑧ docs/comparison/ に1ページ記述（次 Phase のブロック条件）
```

②〜⑤を通しで回すなら `make PLATFORM=vertex phase-all`（teardown は含まない）。

上の並びは5基盤で共通だが、**各ステップを何で満たし何を見て合否を判定するかは基盤ごとに違う**。着手時は対応する runbook を開く。

| Phase | 基盤 | runbook | 状態 |
|---|---|---|---|
| 1 | Vertex AI | [runbooks/動作検証-vertex.md](./runbooks/動作検証-vertex.md) | ✅ **完了**（2026-08-01） |
| 2 | SageMaker AI | [runbooks/動作検証-sagemaker.md](./runbooks/動作検証-sagemaker.md) | ← 次（残り1基盤） |
| 3 | Databricks | [runbooks/動作検証-databricks.md](./runbooks/動作検証-databricks.md) | ✅ **完了**（2026-08-01・Free Edition） |
| 4 | Azure ML | [runbooks/動作検証-azureml.md](./runbooks/動作検証-azureml.md) | ⛔ **保留**（無料試用版では AML compute が offer 対象外。Pay-As-You-Go へのアップグレード待ち） |
| 5 | Snowflake | [runbooks/動作検証-snowflake.md](./runbooks/動作検証-snowflake.md) | ✅ **完了**（2026-08-01・トライアル） |

**Phase 1 で踏んだ穴は [動作検証-vertex.md §0](./runbooks/動作検証-vertex.md) に集約してある。**
他基盤の着手前にそこを読む（同じ穴が空いている前提で先に潰す）。

### 配布物の準備（Phase 着手前）

```bash
make docker-build            # Tier A 学習イメージ
make docker-build-serving    # Tier A 推論イメージ（3契約を1イメージで）
make wheel                   # Databricks 配布用 wheel（stamp してからビルド）
make sf-package              # Snowflake stage 配布用 zip（同上）
```

`wheel` / `sf-package` が `stamp-revision` に依存しているのは、**配布物の中に .git も `CODE_REVISION` も無い**ため。焼き忘れるとジョブ起動後に `CodeRevisionError` で落ちる。

- セッション終了時は必ず `make PLATFORM=<p> phase-teardown && make ENV=<env> tf-destroy`（マネージドエンドポイントは常時課金。`tf-destroy` は残留検査まで連鎖する）。
- 学習ローカル基準（Phase 0）: `make dataset-export && make train`。RandomForest の健全性チェックも回すなら `make BASELINE=1 train`。

## テスト

```bash
make test     # pytest（クラウド資格情報なしで走る。詳細は 07_test_strategy.md）
```

## 作業終了

```bash
git diff --check
git status --short
```

- 実行した検証を task の `Verification` に残す。
- 未解決事項は task の `Notes` または `docs/tasks/02_backlog/` に移す。
- 確定した仕様・手順・判断は docs 本体、runbook、ADR へ昇格する。
- 挙動を変えたら、この文書を含む連動 docs を同一コミットで更新する。
