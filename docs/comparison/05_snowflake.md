# Snowflake

> 実測日 2026-08-01 / トライアル（Standard・Asia Pacific Tokyo）/ `ABCDEFG-HI12345`
> サーバー版 `10.26.102` / `snowflake-ml-python 1.48.0`
> 実行手順と合否判定: [../runbooks/動作検証-snowflake.md](../runbooks/動作検証-snowflake.md)

Tier: B（データ基盤内蔵型・統一単位 = stage へ置く zip）

## 構造仮説（着手前の予想・実測で検証する）

- 器（Database / Schema / Warehouse / Role / Stage / Network Rule）は完全にコード化できるが、
  Model Registry 側が SQL/SDK に残る
- 外部 PostgreSQL（Neon）到達に external access integration が要る = 到達コストが最も重い
- トライアル期限が実質のタイムボックス。分散実行せず一気に完走する
- ⚠️ データ層の罠: 公式 quickstart は Kaggle 版 California Housing。配管だけ借りる

| 予想 | 実測 | 差分 |
|---|---|---|
| 器はコード化できるが Model Registry は SDK に残る | **当たり**。11リソースを Terraform で作れた。登録は SDK のみ | 予想どおり |
| EAI で Neon 到達コストが最も重い | **外れ。到達手段が存在しない。** `External access is not supported for trial accounts` | 「重い」ではなく「不可」。契約段階が機能を切っている |
| データ層の罠（Kaggle 版混入） | **回避成功**。sklearn 版を投入し RMSE 完全一致 | 予想が有効に働いた例 |
| — （予想していなかった） | **その external access 制限が、モデル登録まで止めていた** | 到達不能の影響範囲が Neon だけではなかった（下記「詰まった点」が本フェーズ最大の発見） |

## 完了条件8項目

| # | 項目 | 結果 |
|---|---|---|
| ① | terraform apply | ✅ **4回目で成功**（11リソース / 2.3s） |
| ② | 学習ジョブ成功（失敗試行も記録済み） | ✅ **attempt 2 で成功**（再構築後の再実行は attempt 3 / 53.4s） |
| ③ | Neon へメトリクス到達 | ✅ **collected**（仮説どおり。stage → `make collect`） |
| ④ | モデル登録 | ✅ **attempt 5 で成功 / 78.7s**（4回失敗の原因は下記） |
| ⑤ | 1件オンライン推論 | ✅ **22.3s / 予測値 `4.183217948107466`** |
| ⑥ | terraform destroy | ✅ 11リソース / 8.0s |
| ⑦ | 残留リソース記録 | ✅ FAIL 0件（stage blob + Fail-safe の固定行） |
| ⑧ | 比較レポート（本ページ） | ✅ |

## 実測値

| 指標 | 値 | 出典 |
|---|---|---|
| RMSE | **0.4368055090296257** | metric parity |
| 予測値 | **4.183217948107466** | 同上 |
| code_revision | `35d48cbb…`（Snowflake の run） | 同上 |
| **run friction**（実行時） | train **2** / register **5** / deploy 1 / predict 1 | permission friction |
| failure_class の内訳（run のみ） | `network` × 1（**誤分類**・実体は package）/ `sdk` × 4 | 同上 |
| **infra friction**（構築時） | apply **4回**（うち失敗3） | infra_events |
| stage 別所要 | train 53.4s / register 78.7s / deploy 0.5s / predict 22.3s | stage 別所要 |
| Neon 到達経路 | **collected 2 / direct 0** | 到達経路内訳 |
| apply / destroy | 2.3s / 8.0s・**11リソース** | infra_events |
| destroy 後の残留 | stage blob + **Fail-safe 7日（消せない）** | teardown 品質 |
| アイドル時課金 | warehouse の auto-suspend のみ | 手記述 |

集計境界は [00_method.md](./00_method.md)「friction の集計境界」に従い **run と infra を別掲**。
合算しない（apply の失敗3回には権限モデルの実測が含まれるが、実行時の摩擦とは意味が違う）。

### metric parity の証明経路

**3環境を直接比較したわけではない。** 実際に測ったのは次の3組で、ローカルを基準点にした推移で成立している。

| 比較 | 依存 | 結果 |
|---|---|---|
| ローカル == Vertex | sklearn **1.9** 系 | 16桁一致 |
| ローカル == ローカル | sklearn 1.9 → **1.8**（依存統一時の回帰確認） | 16桁一致 |
| ローカル == Snowflake | sklearn **1.8** 系 | 16桁一致 |

**Vertex と Snowflake は依存セットも SHA も異なる**（Vertex は依存統一前の実行）。
同じ表に単一の `code_revision` を書くと3環境が同一SHAで走ったように読めるので、
基盤ページごとに当該 run の SHA を書く。

この推移が崩れるのは、**Vertex を sklearn 1.8 で測り直して 16桁一致しなかった場合**。
その条件だけが「測り直さなかった判断」を覆す。

## 詰まった点（一次記録）

### ④ register が4回失敗 —— 原因は external access（PyPI）

**トライアルの external access 制限が、Neon 到達だけでなくモデル登録まで止めていた。**

```
アカウント capability: ENABLE_PIP_ONLY_PACKAGING = true
  ↓ SDK が MANIFEST.yml に pip 経路を書く
  artifact_repository_map: {pip: SNOWFLAKE.SNOWPARK.PYPI_SHARED_REPOSITORY}
  ↓ CREATE MODEL 時にサーバーが PyPI を取りに行く
  ↓ トライアルは external access 不可
603 (XX000) SQL execution internal error / 300002
```

**クライアント側は packaging も upload も完全に成功する**ため、エラーからは何も分からない。
`log_model(..., conda_dependencies=[...])` を渡して Anaconda channel 経路にすると通る。

特定までに7つの仮説を実測で潰した。**潰した順に価値がある**ので全部残す。

| # | 仮説 | 反証 |
|---|---|---|
| 1 | 権限不足 | `SHOW GRANTS` に `CREATE MODEL on SCHEMA` あり |
| 2 | 一過性 | 4回とも別 incident ID で同一エラー |
| 3 | 表と同名（`CALIFORNIA_HOUSING`）の衝突 | 別名 `CH_REGRESSOR` でも同じ |
| 4 | LightGBM Booster 固有 | 素の `LinearRegression` でも同じ |
| 5 | channel に sklearn 1.8 が無い | `information_schema.packages` に存在 |
| 6 | `snowflake-ml-python 1.49` が channel に無い | **無いのは事実**（channel 最大 1.48）だが 1.48 でも同じ |
| 7 | 一時ステージ経路 | 永続ステージへ差し替えても同じ |

**誤った推論を1つ記録する。** 空の永続ステージが `398500 Missing manifest file` を返し、
一時ステージが内部エラーだったことから「ステージ種別が原因」と一度結論づけた。
これは誤りで、空ステージは MANIFEST 検証で**早期に落ちていただけ**で比較になっていなかった。
決着したのは、実際に生成された `MANIFEST.yml` を stage から取得して中身を読んだとき。

query_id: `01c61788-0004-4e7d-0005-026200014662` /
incident: 5885109 / 6851289 / 3390993 / 1354170（サポート照会用）

### ① apply が4回

| # | 事象 | 対処 |
|---|---|---|
| 1 | `SNOWFLAKE_ACCOUNT`（connector 用 `<org>-<account>`）を provider v2 が拾い experiment を要求 | terraform 実行時のみ `env -u SNOWFLAKE_ACCOUNT`。**同じ「アカウント」でも provider と connector で形式が違う** |
| 2 | `External access is not supported for trial accounts` | EAI / network rule / secret を作らない構成へ |
| 3 | `Cannot grant or revoke USAGE on an internal staging location` | 内部ステージは READ/WRITE のみ。**モジュールの実装バグを実測で発見** |
| 4 | grant が `object does not exist or not authorized` | 一過性。再実行で解消 |

### ② train が2回

`255002: Optional dependency: 'pandas' is not installed` —— 実際に足りていたのは **pyarrow**。
`session.table().to_pandas()` は connector の pandas 経路を通り pyarrow を要求する。
「Parquet を読むのは Tier A だけ」という設計判断が誤りだった。

**この失敗は `failure_class='network'` と誤分類された**（実体は `package`）。
分類器のヒント語がエラー全文に当たった結果。未分類にはならない設計なので致命ではないが、
内訳を読むときは `error_excerpt` と突き合わせる必要がある。

### 依存の再統一（要件の破綻条件に該当）

`snowflake-ml-python` は最新でも `scikit-learn<1.9` を要求し、core の `>=1.9,<1.10` と
同居できなかった。要件の「依存を削って再統一」に従い **5基盤とも sklearn 1.8 系へ**。
**RMSE が 1.9 系と16桁一致**することを先に確認したので Vertex は測り直していない。

さらに版は **channel の在庫に合わせる**必要がある（`snowflake-ml-python` は 1.48 が上限）。
**Tier B の依存制約は二重**（SDK の要求版 × channel の在庫）で、
これが Tier A の依存上限まで決めた。

## 撤退時に残ったもの

```
[WARN] snowflake/stage_file: BLOBS
[WARN] snowflake/fail_safe: 7 days   # 設定で消せない
-- FAIL/ERROR 0 件 / 全 2 件
```

- **課金が続くものは無し**（warehouse は destroy 済み・FAIL 0件）
- `fail_safe` は**消せない固定行**。Tier A の残留と同じ土俵で数えない
- `stage_file: BLOBS` は本ラボの stage ではない。**Vertex で入れた「ラボ資産だけを数える」
  絞り込みが Snowflake 側に未適用**という検査の不備（次フェーズ前に入れる）

### 残留検査は「撤退で消える権限」に依存してはいけない

`check_residual` が adapter と同じ `MCML_DEV_ROLE` で接続していたため、destroy 後は
`Role does not exist` で**検査自体が成立しなかった**。ロールを名乗らない接続に変更。
**ロールもカタログの中にあり destroy の対象になる**という Tier B 固有の落とし穴。

## 5基盤比較への寄与

| 観点 | Snowflake の位置 |
|---|---|
| 実行資源 | **DDL + CALL のみ**。ジョブ資源が存在しない |
| Neon 到達 | **collected**（トライアルでは direct の手段が存在しない） |
| デプロイ | **既定バージョンの切り替えのみでリソースを作らない経路が存在する**（今回はこの経路で 0.5s。SPCS を選べば専用資源が立つが未測定） |
| アイドル課金 | warehouse の auto-suspend のみ |
| 依存の自由度 | **最も低い**。SDK の要求版 × channel の在庫が core の版まで決めた |
| 登録の情報量 | **最も多い（実測）**。artifact URI では足りず「復元したモデル + 入力サンプル + conda 依存」を要求 |

最後の行は **失敗ログが根拠**。`ValueError (2110): Either of signatures or sample_input_data
must be provided` と、その後の PyPI 解決失敗が、成功前に要求仕様を露出させた。
**Tier A は artifact の URI を受け取るだけで中身を読まない。Tier B は登録が
サーバー側のパースと依存解決を伴う**ため、クライアント側の準備が完璧でも通らないことがある。
これは登録が一度で成功していたら見えなかった性質で、失敗の方が証拠として強い。
