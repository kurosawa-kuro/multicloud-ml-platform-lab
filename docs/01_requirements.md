# 01 要件

> 出典: 設計ブレスト v2（[archive/managed-ml-platform-comparison-brainstorm-v2.md](./archive/managed-ml-platform-comparison-brainstorm-v2.md)、v1 は [archive/managed-ml-platform-comparison-brainstorm.md](./archive/managed-ml-platform-comparison-brainstorm.md)）から蒸留。実装手段・構成は [02_architecture.md](./02_architecture.md) に分離。

## 目的

同一のデータセット（California Housing・固定具）と同一SHAの学習コードを **5つのマネージドML基盤** で学習・登録・推論・撤退まで実行し、**選定時に効く差分を実測して文書化する**。

- 対象基盤は2階層: **Tier A（コンテナ実行型）** = Vertex AI / SageMaker AI / Azure ML、**Tier B（データ基盤内蔵型）** = Databricks / Snowflake。5基盤は同一カテゴリではなく、この階層差自体が比較対象。
- 主成果物はコードではなく **比較レポート＋選定チェックリスト**（`docs/comparison/selection-checklist.md`）。コードは計測装置という位置付け。
- 測るのは「権限設計に要した試行回数」「IaCで管理できる境界」「外部DB（Neon）への到達経路と設定の重さ」「撤退後の残留リソース」「アイドル時課金の構造差」。
- **測れないことを先に宣言する**: California Housing 約2万行では「データの近くで計算する」性能優位は測れない。性能差ではなくガバナンスとIaC境界を測る、とレポート冒頭（method）で宣言する。測ったふりはレポート全体の信頼を落とす。
- 失敗を一級データとして記録する。「最小権限で学習ジョブが通るまでに何回直したか」が最も転用価値の高い情報。
- 計測データの到達点は Neon PostgreSQL に一元化し、**到達可否そのものを比較軸とする**（Tier A=通常egress / Tier B=宣言的な統合オブジェクト経由）。

## 範囲

対象:

- 5基盤での **学習ジョブ投入 / モデルレジストリ / エンドポイント（推論）/ IAM・権限モデル** の比較
- 2階層分割から出る比較軸: 権限モデルとデータガバナンスの一体性 / 外部ネットワークへの出方 / モデルの所在（独立レジストリかカタログ内か）/ 撤退時に消えないもの
- 統一単位: **`src/core/ml` の同一 git SHA**（Tier A は同一コンテナイメージ、Tier B は同一Pythonパッケージとして配布）
- 全基盤から Neon への計測データ書き込み（直接到達不能時は fallback 収集し、その事実も記録）
- 実行・インフラ操作・コストの計測記録（run / infra_event / cost の分離記録）
- destroy 後の残留リソースの5基盤横断での列挙・記録（`check-residual`）
- 比較レポート: 前提宣言（method）+ 基盤別1ページ + 選定チェックリスト + 残留リソース比較表
- 除外判断の明文化（何を作らなかったかを README に書く）

非対象:

- **Feature Store（5基盤すべてで不使用**: Vertex AI FS / SageMaker FS / Azure ML FS / Databricks Feature Engineering / Snowflake FS）。特徴量更新もオンライン/オフライン整合性の問題も存在しないため
- ドリフト検知・モデル監視・バッチ/オンライン両系統（同上の理由）
- Pipelines 機能の比較実装（「機能を呼んだだけ」になるため）
- 機能比較マトリクスの作成（`thoughtworks/mlops-platforms` が公開済みで劣化コピーになる。参照先として使い、成果物は実測値に限定）
- データ近接の性能比較（2万行では測れない。上記「測れないことの宣言」）
- 精度の優劣比較（メトリクスは同一SHA再現の確認用のみ）
- Snowflake SPCS を主経路にすること（「Snowflakeを別のKubernetesとして使った」記録にしかならない。warehouse 実行が主経路、SPCS は差分メモ止まり）
- pg_mooncake / 列指向分析（**非採用決定済み**・2026-07-31。合成ログでは現実のクエリパターンが無く「作った問題に自分で答える」構造になるため。task も削除済み）
- 計測データ保存先 DB の Sakura VPS 置換検討（別プロジェクト）

## ユーザー

- owner 本人。用途は次の3つ:
  - ML基盤選定の実測ベースの判断材料（何を先に潰すかを決められる立場の証拠）
  - コンサル文脈への転用（選定チェックリスト・権限フリクション実測・残留リソース比較）
  - 資格学習との併走（PMLE / Databricks ML Associate / SnowPro Core と各フェーズを対応付け）

## ユースケース

| ID | ユースケース | 成功条件 |
|---|---|---|
| UC-001 | 各基盤で学習ジョブを最小権限で通す | 成功 run と、そこに至る全失敗 run（iam / quota / container / package / network 等の分類付き）が記録されている |
| UC-002 | 各基盤の1フェーズを完走する | フェーズ完了条件8項目（下記「制約」）を満たし、比較レポートの1列が実測で埋まる |
| UC-003 | 全基盤から Neon へ計測データを到達させる | direct / collected の別が記録され、SELECT だけで5基盤の比較ができる |
| UC-004 | destroy 後の残留リソースを比較する | 「destroyしても消えないもの」の5基盤比較表が実測で埋まる（Tier B のガバナンス機能による残留仮説を実測で検証） |
| UC-005 | 基盤選定の判断を下す | selection-checklist の判断軸（複雑性 / IaC範囲 / IAM修正回数 / 依存制約 / Neon到達 / モデル所在 / 課金構造 / 残留 等）が埋まり、埋まらない軸は理由が書かれている |

## 制約

- **予算**: Tier A 各 ¥2,000/月、Tier B 各 ¥1,000/月、合計上限 ¥8,000/月。超過したら Azure（Phase 4）を切り離す（5基盤は目的ではない）。
- **比較成立の担保**: 全基盤の run が同一 `code_revision`（`src/core/ml` の git SHA）であること。さらに**同一SHAで RMSE が全基盤一致**すること。不一致は基盤の差ではなく実装漏れであり、原因判明まで次へ進まない。
- **統一単位**: Tier A は BYOC 統一（SageMaker script mode に逃げない）。Tier B は同一パッケージ（wheel / stage upload）。統一不可ならその事実を発見として記録し比較軸から外す。
- **依存最小**: `src/core/ml` の依存は lightgbm / scikit-learn / pandas / pyarrow のみ。依存が増えるほど Tier B（Anaconda channel 限定・ML Runtime 衝突）で崩れる。
- **実施順序**: Phase 0 ローカル基準（PMLE前）→ Phase 1 Vertex（PMLE前・2セッション・既存資産流用）→ Phase 2 SageMaker（PMLE後）→ Phase 3 Databricks（ML Associate 学習と併走）→ Phase 4 Azure ML（Phase 3 完了時に go/no-go）→ Phase 5 Snowflake（SnowPro Core 着手をトリガ、トライアル期限内に一気に実行）。
- **各フェーズ完了条件（全基盤共通・8項目）**: ① terraform apply ② 学習ジョブ成功（失敗試行も記録済み）③ Neon へメトリクス到達（direct / collected を記録）④ モデル登録 ⑤ 1件オンライン推論 ⑥ terraform destroy ⑦ 残留リソース記録 ⑧ 比較レポート1ページ記述。**⑧は次フェーズのブロック条件**。
- **推測設計の禁止**: 仕様変更が入りやすい領域（各基盤の外部到達性・トライアル条件・Terraform 対応範囲等）は各 Phase 開始時に現行ドキュメントで確認する（backlog の precheck タスク参照）。
- **破綻条件**:

| 条件 | 対応 |
|---|---|
| Phase 1 が2週間超 | 独立プロジェクト化をやめ、既存リポジトリへ吸収 |
| 5基盤で RMSE が不一致 | 実装漏れ。原因判明まで次へ進まない |
| `code_revision` が基盤間で不一致 | 比較不成立。contract test で自動検出 |
| Tier B で `src/core/ml` がそのまま動かない | 依存を削って再統一。削れないなら「依存制約」を発見として記録し、その基盤を別枠にする |
| Snowflake から Neon へ到達できない | fallback（JSONL収集）に切替。到達不能自体を結果として記録 |
| フェーズ終了時にレポート未記述 | 次フェーズをブロック |
| 合計コスト ¥8,000/月 超過 | Azure を切り離す |
| Tier A で BYOC統一を断念 | 事実を記録し比較軸から外す |

## Critical User Journey / Golden Path

このプロダクトが「使われて価値が出る」中核の一本道。1基盤分のフェーズを端から端まで完走し、計測データが Neon に到達し、比較レポートの1列が実測で埋まることが Golden Path。個別ステップが動いても、この一本が切れていたら（特にレポート未記述なら）未達とみなす。

- 一次ユーザー: owner（ML基盤選定の実測データ収集という目的の journey）
- 完了状態（この journey のゴール）: 対象基盤のレポート1列が実測値で埋まり、Neon の SELECT だけで他基盤と比較できる状態

| # | ステップ | 成功条件（観測可能な状態） |
|---|---|---|
| 1 | terraform apply で基盤構築 | infra_event に apply の所要・リソース数が記録される |
| 2 | 学習ジョブを投入し成功させる | 成功 run と全失敗 run（failure_class 付き）が記録される |
| 3 | Neon へメトリクス到達・モデル登録・1件オンライン推論 | write_path（direct / collected）が記録され、同一SHAで RMSE が一致し、推論レスポンスを取得 |
| 4 | terraform destroy → 残留リソース記録 → レポート1列記述 | 残留リソースが記録され、`docs/comparison/` の当該基盤列が埋まる |

- この Golden Path は、基礎設計（[02_architecture.md](./02_architecture.md)）でどの構成要素・境界を通るかに写像する。
- リリース運用（[08_release_runbook.md](./08_release_runbook.md)）では、この一本をデプロイ後 smoke / ロールバック判定の基準にする。

## 関連タスク

- 要件追加・変更は、まず `docs/tasks/03_active/` または `docs/tasks/02_backlog/` に task として記録する。
- 確定した要件だけをこの文書へ反映する。
- 要件変更に伴う未実装作業は `docs/tasks/README.md` から追跡できる状態にする。
- 未確定論点（各 Phase 開始前の precheck。実アカウントが要るものだけが残っている）:
  - databricks-phase-precheck.md
  - snowflake-phase-precheck.md
- 確認済み（2026-07-31・結論は [02_architecture.md](./02_architecture.md) と各モジュールのコメントへ反映済み）: Azure ML Workspace の必須依存（Storage / Key Vault / **App Insights** / identity。ACR は任意）／ Neon pooled endpoint の仕様
- 既存資産の流用元と移植時の改変の記録: reuse-asset-import-map.md
