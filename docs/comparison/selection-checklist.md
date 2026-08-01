# 選定チェックリスト（主成果物）

> **5基盤すべて完走（2026-08-01）**。全列が実測で埋まった。
> 一次データは Neon の `ml_runs` / `infra_events` と各基盤ページ（`01`〜`05`）。

このプロジェクトの最終成果物。「どの基盤を選ぶか」を判断するときに、
実測値で答えられる状態にする。埋まらない軸は空欄のままにせず、
**なぜ埋まらなかったかを書く**（測れなかったことも情報）。

## 前提: 精度も予測値も5基盤で一致した

| 指標 | 5基盤の値 |
|---|---|
| RMSE | **0.4368055090296257**（5基盤とも同一・ローカル基準値と一致） |
| 1件推論の予測値 | **4.183217948107466**（5基盤とも同一） |

**だから以下の差分だけが選定材料になる。** 同一データ・同一SHAで回した結果、
モデルの出力に差は出なかった。差が出たのは**そこへ到達するまでの経路**。

## 判断軸

| 判断軸 | Vertex AI | SageMaker | Databricks | Azure ML | Snowflake |
|---|---|---|---|---|---|
| Tier / 統一単位 | A / イメージ | A / イメージ | B / **wheel** | A / イメージ | B / **zip** |
| 実行契約の複雑性 | 環境変数（`AIP_MODEL_DIR`） | 固定パス（`/opt/ml`） | wheel + ジョブ内 MLflow | **マウント宣言 + 2階層 + traffic** | SQL + stage |
| Terraform でカバーできる範囲 | 17リソース | 15リソース | 7リソース | 10リソース | 11リソース |
| SDK/CLI/SQL に残る範囲 | 登録・デプロイ | 登録・承認 | MLflow 登録・Serving | 登録・デプロイ・**provider 登録** | **Model Registry 全体** |
| 最小権限で通るまでの修正回数 | **0** | **0** | **0** | **1**（`Storage Blob Data Contributor`） | **0** |
| failure_class の内訳 | package 1 | container 1 / sdk 1 | **sdk 6** | sdk 1 | sdk 4 / network 1 |
| 依存パッケージの制約 | — | — | **psycopg 不在**（到達経路を決めた） | sklearn を serving と同一 minor に固定 | `snowflake-ml-python` の版に追随 |
| 外部DB（Neon）到達可否と経路 | **direct** | **direct** | **collected**（psycopg 不在） | **direct** | **collected**（EAI がトライアルで作れない） |
| モデルの所在 | Model Registry | Model Package（**承認フロー**） | Unity Catalog | Workspace（**版が自動採番**） | カタログ内オブジェクト |
| アイドル時課金の構造 | Endpoint **常時課金** | Endpoint **常時課金** | Serving は **scale-to-zero** | Endpoint **常時課金** | Warehouse **自動サスペンド** |
| 撤退の容易さ / 残留リソース | WARN 1（登録モデル） | WARN 3 | **0件** | 0件 + **IaC 管理外 1** | FAIL 0（stage blob + Fail-safe） |
| 学習ジョブ投入から成功までの実時間 | 259.3s | 約5分 | 55.1s | 約3分50秒 | 53.4s |
| apply 試行回数 | **1回** | **1回** | 2回 | **3回**（契約ゲート2枚） | **4回** |
| destroy 試行回数 | **1回** | 2回 | 2回 | **3回 + 手動 `az group delete`** | 1回 |
| 初期構築の総量 | 17リソースが1回で通る | 15リソース | 7リソース（Free Edition 制約） | 10リソース + **サブスク側準備2つ** | 11リソース（4回の試行） |
| **tfstate の置き場** | 自前 GCS | 自前 S3 | **持てない**（Neon へ） | 自前 Blob | **持てない**（Neon へ） |

## 使い分けの結論

「どれが優れているか」ではなく**どういう条件のときにどれを選ぶか**の形で書く。

| 条件 | 選ぶもの | 理由（実測） |
|---|---|---|
| **とにかく早く1本通したい** | **Vertex AI** | apply / destroy とも試行1回。契約ゲートも権限追加もゼロ。5基盤で唯一「詰まらなかった」 |
| ジョブから外部DBへ直接書きたい | Vertex / SageMaker / Azure ML | Tier A は3基盤とも `direct`。Tier B は2基盤とも `collected` に落ちた |
| **アイドル課金を避けたい** | Databricks / Snowflake | Serving は scale-to-zero、Warehouse は自動サスペンド。Tier A の Endpoint は3基盤とも常時課金 |
| 撤退の確実さを重視 | **Databricks** | 残留 0 件。Tier A は3基盤とも何かしら残った |
| データが既に基盤内にある | Snowflake / Databricks | 統一単位が zip / wheel でコンテナ運用が要らない |
| モデルに承認フローを挟みたい | SageMaker | Model Package の `Approved` 状態が標準で入る。他4基盤には無い |
| **新規契約から始めるなら避ける** | Azure ML | **契約ゲートが2枚**（offer / プラン）。どちらも Terraform の外側で人手の契約変更が要る |

## 予想と実測の差分

着手前の構造仮説（各基盤ページに記載）が当たったか外れたか。
外れた項目こそが、このプロジェクトで新しく得た知識。

| 仮説 | 実測 | 差分 |
|---|---|---|
| Databricks が Terraform 網羅度最大 | **外れ。7リソースで5基盤中最少** | Free Edition は serverless 専用でクラスタ等が存在しない。「網羅度」は基盤の性質ではなく**契約プランで変わる** |
| Snowflake は器は書けるがレジストリが SQL/SDK に残る | **当たり** | 11リソースを Terraform で作れたが Model Registry は SDK のみ。しかも登録は4回失敗した |
| Azure ML は周辺依存で初期構築量最大 | **外れ。10リソースで Vertex の 17 より少ない** | Vertex は API 有効化8件を IaC が持つが、Azure の同等物（provider 登録）は **IaC の外**。数えるならスコープを揃える必要がある |
| Vertex は ML 固有リソースの Terraform 対応が限定的 | **当たり** | 登録とデプロイは SDK 側。ただしそれ以外が滑らかで、総合では最も摩擦が少なかった |
| Tier A は direct / Tier B は collected | **当たり（5基盤とも予想どおり）** | ただし理由が基盤ごとに違う（Databricks は psycopg 不在、Snowflake は EAI がトライアルで作れない） |
| —（予想していなかった） | **契約プランが機能を切る**（Snowflake の EAI / Azure の Spot 枠） | **「できない」の正体が技術ではなく契約**という共通パターン。Tier をまたいで2基盤で起きた。事前調査では見えず、実際に触って初めて分かる |
| —（予想していなかった） | **Tier B は tfstate を自前で置けない** | UC Volume も Snowflake stage も Terraform backend ではなく、オブジェクトストレージが外に無い。「データが基盤の中にある」という Tier B の性質が、**IaC の足場にまで及ぶ**（2026-08-01 実測。中立の置き場として Neon の pg backend へ移した） |

## 測れなかったこと

`00_method.md` の宣言と対応させる。ここが空だと「全部測れた」と誤読される。

- **本番規模の性能・スケール**: 全基盤で1ノード / 20,640行。同時実行も大規模データも測っていない
- **コスト実額の横並び**: 契約形態（トライアル / Free Edition / 従量課金）が基盤ごとに違い、同一条件の請求額を比較できない
- **チーム運用**: 単一 owner での実測。権限委譲・監査・複数人の同時作業は範囲外
- **SageMaker の Spot 利用可否**: 実装にも記録にも無く、Azure の Spot 制約と対比できなかった
- **長期運用の残留**: 撤退直後のみ観測。数日後に自動削除されるものは区別できていない
