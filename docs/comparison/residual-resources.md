# 撤退後の残留リソース比較

> **5基盤とも撤退完了（2026-08-01）**。各 `terraform destroy` 直後に観測した値のみを載せる。
> **後日まとめて調べ直すことはできない**（時間が経つと自動削除されたものと
> 元から無かったものを区別できなくなる）ため、以降この表は追記のみで書き換えない。

一次データは `scripts/check_residual.py` の出力と `infra_events.residual_resources`。
このページ自体が、5基盤を触った人にしか書けない転用可能な成果物になる。

## 判定原理

```text
terraform state に不在 × クラウドに実在 = 孤児
```

分類:

- **FAIL**: 課金が続く / destroy を阻害する
- **WARN**: 残るが軽微
- **ERROR**: API 無効・権限不足で検査自体ができなかった（＝残留の有無が不明）

ERROR を WARN に丸めない。「調べられなかった」と「無かった」は別。

## 結論を先に

**FAIL は5基盤とも 0 件。** 課金が続く残留はどこにも出なかった。
差が出たのは **WARN の中身と、撤退に手作業が要ったかどうか**。

| | Vertex AI | SageMaker | Databricks | Azure ML | Snowflake |
|---|---|---|---|---|---|
| FAIL | 0 | 0 | 0 | 0 | 0 |
| WARN | 1 | 3 | **0** | 0 | 2 |
| 手動削除の要否 | 不要 | 不要 | 不要 | **要**（1件） | 不要 |
| destroy 試行回数 | **1回** | 2回 | 2回 | **3回 + 手動** | 1回 |

## Tier A（コンテナ実行型）

| 基盤 | 残ったもの | 分類 | 課金継続 | 手動削除の要否 |
|---|---|---|---|---|
| Vertex AI | 登録モデル `mcml-california-housing:1` | WARN | なし | 不要（残しても無害） |
| SageMaker AI | Endpoint Config `mcml-dev-config-99fc40091fea` ほか計3件 | WARN | なし | 不要 |
| Azure ML | **`Application Insights Smart Detection`**（App Insights 作成時に Azure が自動生成・**Terraform 管理外**） | — | なし | **要**。RG 削除を阻害するため `az group delete` が要る |

Tier A の典型的な残留候補: マネージドエンドポイント（常時課金）/ オブジェクトストレージ /
コンテナレジストリのイメージ / ログ / 論理削除されたシークレット。
**このうち実際に残ったのは登録モデルと設定オブジェクトだけで、課金の続くものは出なかった。**

**Azure ML の予想が外れた点**: 事前に「Key Vault の論理削除が FAIL で必ず残り
`az keyvault purge` が要る」と予想していたが **残留 0 件**だった。provider の
`purge_soft_delete_on_destroy = true` が destroy 時に purge していたため。
つまり **「Azure 固有の残留」は基盤の性質ではなく IaC 設定の有無**で決まる。

## Tier B（データ基盤内蔵型）

| 基盤 | 残ったもの | 分類 | 課金継続 | 手動削除の要否 |
|---|---|---|---|---|
| Databricks | **無し**（`findings: []`） | — | なし | 不要 |
| Snowflake | stage 成果物 `BLOBS` | WARN | なし | 不要 |
| Snowflake | **Fail-safe 7日** | WARN | なし | **不可**（設定で消せない） |

Tier B の残留は Tier A と**質が違う**。Time Travel / Fail-safe /
カタログ内オブジェクト / stage 成果物は、リソースというよりデータの保持期間として残る。
同じ土俵で数を比べず、種別を分けて記録する。

**Snowflake の Fail-safe は「消せない残留」という独自カテゴリ。** 手動削除の可否が
そもそも無い（アカウント側の仕様）ので、Tier A の WARN と同じ土俵で数えない。
**Databricks が5基盤で唯一の残留ゼロ。**

## destroy を阻害したもの

destroy が1回で通らなかったケース。順序依存・保護設定・手動掃除が要った箇所。
`infra_events` の attempt と対応させる。

| 基盤 | 阻害要因 | 回避手順 |
|---|---|---|
| Vertex AI | **無し**（1回で完了・20.2s） | — |
| SageMaker AI | Model Package が残っていて失敗 | 削除してから再 destroy（attempt=2） |
| Databricks | モデル版が残っていて拒否 | 削除してから再 destroy（attempt=2） |
| Azure ML | **IaC 管理外の自動生成リソース**が RG に残り、RG 削除が拒否（817.5s / 760.9s で失敗） | `az group delete --name mcml-dev-rg --yes` → `terraform destroy` で state 同期（22.8s） |
| Snowflake | **無し**（1回で完了・8.0s） | — |

**Azure の失敗はガードレールが働いた結果でバグではない。**
`prevent_deletion_if_contains_resources = true` を明示設定しており
（「残留を静かに握り潰すと `check_residual.py` の一次データが歪む」ため）、
**IaC 管理外の自動生成リソースを検知して止まった**。`false` にすれば1回で終わるが、
それはこの残留を隠すことになる。

## 撤退容易性の結論

「消したつもりで消えていないもの」がどれだけ出るか。
個人開発でも組織でも、選定時に効く割に事前には分からない情報。

1. **課金が続く残留は5基盤ともゼロだった。** 事前の最大懸念（Endpoint の消し忘れ）は、
   teardown を手順に組み込めば防げる。**残留の怖さは課金ではなく「再作成を塞ぐこと」**に移る。
2. **destroy が1回で通ったのは Vertex と Snowflake の2基盤だけ。**
   残り3基盤は「先に消すべきもの」が残って失敗した（Model Package / モデル版 / 自動生成リソース）。
   **撤退手順の先頭に「何を先に消すか」が要るかどうか**が実務上の差になる。
3. **手作業が要ったのは Azure ML だけ。** しかも原因は IaC 管理外の自動生成リソースで、
   事前に予測できない。**IaC の外で勝手に作られるものがあるか**は選定時に効く。
4. **Tier B の残留はデータ保持期間として現れる。** Snowflake の Fail-safe のように
   **消す手段が存在しない**ものがあり、Tier A の「消し忘れ」とは対策が違う。

## ⑦ 検査ツールの穴（2026-08-01 修正済み）

`scripts/check_residual.py` は **完全撤収後に実行できなかった**。
親リソース（RG / workspace）が消えていると列挙 API が
`ResourceGroupNotFound` / `ParentResourceNotFound` を返し、**ERROR として記録されていた**。
「調べられなかった」と「残留 0 件」が区別できないという、このページの判定原理そのものに
反する挙動で、**最も確実な「無い」を「不明」に丸めていた**
（当時の Azure の ⑦ は `az group list` / `az keyvault list-deleted` で手動確認した）。

修正後は**親が消えているときだけ ERROR を出さない**。判定は
**実際に観測したエラーコードだけ**（`resourcegroupnotfound` / `parentresourcenotfound`）に
限定している。ここに `"not found"` のような広い文言を足すと、**資格情報エラーまで
「残留ゼロ」に化けて撤収の失敗を見逃す**ため。権限不足・API 無効は ERROR のまま
（`tests/scripts/test_check_residual.py` が両方を固定している）。
