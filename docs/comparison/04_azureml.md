# Azure ML

> **完了（2026-08-01）**。完了条件8項目すべてに到達し、リソースは撤収済み。
> 実行手順は [../runbooks/動作検証-azureml.md](../runbooks/動作検証-azureml.md)。
>
> subscription: 従量課金（`PayAsYouGo_2014-09-01`・`spendingLimit=Off`）/ プラン: Basic /
> region: japaneast / `code_revision = 1aaace38`
>
> **①の途中で一度停止し、契約変更（無料試用版 → Pay-As-You-Go、Free → Basic）を経て再開した。**
> 停止時点の一次記録は付録に残す（書き換えない）。

Tier: A（コンテナ実行型・統一単位 = 学習イメージ）

## 構造仮説（着手前の予想・実測で検証する）

- **周辺依存で初期構築量が最大**（Workspace が storage / key_vault / app_insights / ACR を要求）
- Tier A の3つ目で限界効用が最も低い。Phase 3 完了時に go/no-go を判断する
- FreeTrial 枠のため vCPU quota が他基盤と揃わない可能性がある

外れた場合、外れたこと自体が最も価値のある発見なので、
予想を後から書き換えず「予想 → 実測 → 差分」の形で残す。

| 予想 | 実測 | 差分 |
|---|---|---|
| 周辺依存で初期構築量が最大 | **外れ。リソース数は 10 で Vertex の 17 より少ない** | 「依存が多い = リソース数が多い」ではなかった。Vertex は API 有効化8件を IaC が持つが、Azure の同等物（provider 登録）は **IaC の外**にあるため見かけ上少ない。**数えるならスコープを揃える必要がある** |
| FreeTrial の quota が揃わない可能性 | **当たり。ただし程度が予想以上** | 「他基盤とマシンサイズが揃わない」ではなく **1ノードも起動できない**。quota 不足ではなく offer の対象外 |
| — （予想していなかった） | 失敗した compute 作成が**孤児を残して再実行を塞ぐ** | 撤退手順の先頭に「孤児の確認と削除」が要る基盤がある |
| — （予想していなかった） | **契約ゲートが2枚あった**（offer / プラン） | quota の壁に見えて実体は契約段階の壁。詳細は「経緯」 |
| — （予想していなかった） | **モデルのマウント構造が1段深く serving が 500** | ローカルでは出ない train/serve skew。5基盤で Azure だけが踏んだ |
| `key_vault_soft_deleted` が FAIL で必ず残る | **外れ。残留 0 件** | provider の `purge_soft_delete_on_destroy = true` が purge していた。「Azure 固有の残留」は基盤の性質ではなく **IaC 設定の有無** |

## 完了条件8項目

| # | 項目 | 結果 |
|---|---|---|
| ① | terraform apply | ✅ 10リソース（9 + 予算アラート） |
| ② | 学習ジョブ成功 | ✅ `Completed`。**失敗試行も全件記録**（`ml_runs` 11行） |
| ③ | Neon へメトリクス到達 | ✅ **`write_path='direct'`**（Tier A の仮説どおり） |
| ④ | モデル登録 | ✅ バージョン自動採番（v1 → v3） |
| ⑤ | 1件オンライン推論 | ✅ `/score` 成功（1.09s）。**1回目は失敗**（詰まった点 (3)） |
| ⑥ | terraform destroy | △ **9/10 のみ**。RG だけ残り `az group delete` で完了 |
| ⑦ | 残留リソース記録 | ✅ **IaC 管理外の自動生成が1件**、それ以外は 0 件 |
| ⑧ | 比較レポート（本ページ） | ✅ |

## 実測値

| 指標 | 値 | 出典 |
|---|---|---|
| **RMSE** | **0.4368055090296257** | `ml_runs.metrics` |
| metric parity | ✅ **ローカル基準値と完全一致**（`runbooks/README.md` の 0.4368055090296257） | 同上 |
| r2 / mae | 0.8543973248910732 / 0.2840129606543999 | 同上 |
| best_iteration / rows | 735 / 20,640 | ジョブ標準出力 |
| code_revision | `1aaace38`（成功チェーン） | `ml_runs.code_revision` |
| 到達経路 | `direct`（全ステージ） | `ml_runs.write_path` |
| permission friction | **1**（`Storage Blob Data Contributor`） | 詰まった点 (1) |
| apply 試行（再開後） | **3回**（quota 失敗 → Dedicated 成功 → 枠申請後 LowPriority 成功） | infra_events |
| apply 所要 | 316.6s（失敗）→ **35.7s** → **77.2s** | 同上 |
| destroy 所要 | 817.5s（失敗）→ `az group delete` → **22.8s**（state 同期） | 同上 |
| train 所要 | 4〜5s（adapter 観測）/ **約3分50秒**（Queued → Completed の実時間） | `ml_runs` / `az ml job list` |
| register 所要 | 7.5s → 1.8s → 2.3s | `ml_runs` |
| **deploy 所要** | **547.1s / 513.4s** | 同上 |
| **teardown 所要** | **334.6s / 332.5s** | 同上（`stage=deploy` + `params.action=teardown` として記録） |
| predict 所要 | 1.09s | 同上 |
| compute cluster | `Standard_DS3_v2` / `tier=low_priority` / min 0 / max 1 / idle 300s | `az ml compute show` |
| 予算アラート | `budget_enabled = true`（月次 ¥2,000・実績 50/90%・予測 100%） | terraform output |

**deploy と teardown が突出して重い**（各 5〜9 分）。学習ジョブ本体（4分）より
エンドポイントの作成・削除のほうが長い。Tier A の「常時課金リソースを持つ」性質は、
課金だけでなく**フェーズの実時間**にも効く。

## 経緯 — 契約ゲートを2枚越えた

**どちらも Terraform の外側にあり、コードの修正では越えられなかった。**

| # | 壁 | 症状 | 越え方 |
|---|---|---|---|
| 1 | offer に AML 専用コアが無い | apply が 8/9 で失敗（3回）。`ResourceNotAvailableForOffer` | 無料試用版 → **Pay-As-You-Go**（反映まで約2分） |
| 2 | `TotalLowPriorityCores 0/0` | apply が 9/10 で失敗。`ClusterMinNodesExceedCoreQuota` | プラン **Free → Basic** → 総枠を申請（0 → 8・約90秒で承認） |

### 第1の壁: offer（契約種別）による提供対象外

```
quotaId        FreeTrial_2014-09-01
spendingLimit  On
az quota create --resource-name standardDSv2Family --resource-type dedicated
  → ResourceNotAvailableForOffer
```

原因特定までに3つの事象が重なって見通しが悪かった。**この重なり方自体が記録に値する**。

| # | 事象 | なぜ誤読を招いたか |
|---|---|---|
| 1 | AML の計算枠は `Microsoft.BatchAI` が subscription × region 単位で持つ | `az vm list-usage`（`Microsoft.Compute` 枠）は 4 コア「ある」と表示され、別枠だと気付けない |
| 2 | `az ml compute list-usage` が `TotalDedicatedCores=20` / `standardDSv2Family=6` と返す | **実効値ではない**。`current_value` は全て null、同じ family が `limit=6` と `limit=-1` で重複しており、既定テンプレートと実体が混在している |
| 3 | 失敗した compute 作成が孤児を残す | 2回目以降のエラーが `already exists` に変わり、**本当の原因（quota）が見えなくなる** |

効かないと実測で判明した対処: リージョン変更（offer レベルなので無関係）/
`az ml compute update-quota`（プールが 0 なら再配分できない）/
serverless compute（同じ BatchAI 枠）/ VM サイズ縮小（割当自体が 0）。

**技術的な回避策は1つも効かず、契約変更という人手ゲートが唯一の経路だった。**
アップグレード反映後は AML usages API が `currentValue` を返すようになり、
誤読要因 #2 も同時に解消する（着手可否の判定材料は `limit` ではなく
**`currentValue` が null でないこと**）。

### 第2の壁: dedicated と low-priority は別枠

offer が解けた直後の apply は **9/10 まで進んで compute cluster だけが落ちた**。
module 既定の `vm_priority = "LowPriority"`（コスト最適化）が、別の枠を引いていたため。

| 枠（2026-08-01 実測） | 申請前 | 4 vCPU を賄えるか | 申請後 |
|---|---|---|---|
| `TotalDedicatedCores` | 0 / 20 | ✅ | 0 / 20 |
| `standardDSv2Family`（dedicated） | 0 / 6 | ✅ | 0 / 6 |
| **`TotalLowPriorityCores`** | **0 / 0** | ❌ | **0 / 8** |
| `standardDSv2Family`（lowPriority） | 0 / **-1** | ⚠️ **-1 は無制限ではない**。総枠 0 が効く | 0 / -1 |

**これが誤読要因 #2 の第二形態。** 「family 単位の `limit` を見て足りると判断する」誤りは
offer 反映後も残っており、今度は **`-1`（無制限に見える値）と総枠 0 の組み合わせ**として現れた。
`az vm list-usage` も `Total Regional Low-priority vCPUs 0/3` と表示するため、
**`Microsoft.Compute` 枠を見ても AML の low-priority 総枠 0 には辿り着けない**。

**2枚目は「プラン変更が申請を解錠する」二段構えだった。** 同じ `az quota update` が
Free プランでは即 `QuotaNotAvailableForResource` で拒否され、Basic では
`InProgress` を経て承認された。申請できるのは**総枠のみ**で、family 単位
（`--resource-type lowPriority`）と `TotalDedicatedCores` は受け付けられない。

応急として `vm_priority = "Dedicated"` に落とせば動く（dedicated は既定で `0/20`）が、
それは **Vertex の Spot と実行形態が揃わなくなる**。今回は枠を申請して LowPriority に戻し、
比較条件を揃えた。**「安い実行形態を使うのに契約プランの引き上げが要る」**という非対称は
Vertex（申請なしで Spot 実行・[01_vertex.md](./01_vertex.md)）に対する Azure 側の固有コストとして
`00_method.md`「条件が揃わない箇所」へ転記する。
（SageMaker の Spot 利用可否は未確認。Phase 2 の記録に無く、aws module にも設定が無い。）

### apply 前にサブスクリプション側の準備が2つ要る

リソースプロバイダの登録（7つ）と tfstate 用ストレージのブートストラップ。
前者は GCP では IaC の中（`google_project_service` 8件）にある。

## 詰まった点（実行時・3件）

### (1) データプレーンの RBAC が制御プレーンと別

サブスクリプションの Owner でも `az storage blob upload-batch` が通らない。

```
You do not have the required permissions needed to perform this operation.
    "Storage Blob Data Contributor" ...
```

Storage Account スコープに `Storage Blob Data Contributor` を1つ付与して解消
（RG やサブスクリプションには広げていない）。**permission friction = 1**。
GCP の Phase 1 では同等の追加付与は発生していない。

### (2) ステージを跨ぐと参照が失われ、train からやり直しになる

| 段の単体実行 | 結果 |
|---|---|
| `phase-register` | `成果物 URI が無い` → `azureml://jobs/<job>/outputs/model` を手で組んで回避可 |
| `phase-deploy` | `deploy に渡す参照が未解決` → **CLI に回避手段が無い**（`--artifact-uri` のみ） |

**Azure ML だけ「中断したら train からやり直し」**だった。Vertex は `AIP_MODEL_DIR` が
環境変数として残り、SageMaker は固定パス（`/opt/ml`）なので途中段から再開できる。
Tier A 内で実行契約が3者3様という仮説の、**最も実務コストが高い現れ方**。

**根因は基盤の性質ではなく自前ハーネスの穴だった（2026-08-01 に修正）。**
学習の成功行は**ジョブ側**が書く規約で、ジョブは自分の成果物がどの URI で
参照されるかを知らない。そのため `ml_runs` の train 行は **5基盤とも `params={}`**
で、再開に必要な値がどこにも残っていなかった。adapter が成功行へ params を追記する
ようにして解消（行が無ければ何もしない —— 行を作ると `write_path='direct'` を騙るため）。

実測（同日・修正後）: 学習だけ回して**プロセスを終了**させたのち、
別プロセスで `run_phase.py azureml resume` を**引数なし**で実行し、

```
-- 再開元 train run=c8a1a49a-... artifact=azureml://jobs/heroic_pizza_q42zjqtbx0/outputs/model
[success] register 7.3s / [success] deploy 543.8s / [success] predict 1.2s
```

で完走した（予測値は 4.183217948107466 で5基盤一致値と同じ）。
**中断コストが「全 stage やり直し」から「1 stage」に下がった。**

### (3) モデルのマウント構造が1段深く、serving が 500 を返した

`/health` は 200 なのに `/score` だけ 500。原因はコンテナ側のパス:

```
RuntimeError: モデルが見つかりません:
  /var/azureml-app/azureml-models/mcml-california-housing/2/model.txt
```

登録済みモデルを実際に落として確認すると、中身は `model/model.txt` と1段深い。
**Azure ML はジョブ出力のフォルダ名を保ったままマウントする**ためで、
SageMaker（`/opt/ml/model` 直下に展開）・Vertex（シムが `MODEL_DIR` を直接指す）と異なる。
`core/app/serving/predictor.py` に `locate_model_file()` を足し、直下に無ければ
**子ディレクトリ1段だけ**探すようにして解消（再帰はしない／複数見つかったら拾わない）。

**これは train/serve skew が実クラウドでしか出なかった例。** ローカルの
`make train` は `output_dir` 直下に置くため、5基盤で唯一 Azure だけが踏んだ。

なお deploy 時に SDK が警告を出す:
`Instance type Standard_DS2_v2 may be too small ... Minimum recommended ... Standard_DS3_v2`。
今回は DS2_v2 のまま完走したので変更していない（推奨であって必須ではない）。

## ⑥⑦ teardown と残留

| 残留の種類 | 件数 | 備考 |
|---|---|---|
| `online_endpoint` | **0** | `phase-teardown` で削除済み（332.5s） |
| `key_vault_soft_deleted` | **0** | provider の `purge_soft_delete_on_destroy = true` が purge 済み |
| `registered_model` | **0** | RG ごと削除 |
| **IaC 管理外の自動生成** | **1** | `Application Insights Smart Detection`。**これだけが手作業を要求した** |

**destroy が1回で終わらないのはガードレールが働いた結果で、バグではない。**
`Application Insights Smart Detection` は App Insights 作成時に Azure が自動で作る
アラートルールで **Terraform 管理外**。これが RG に残り削除を拒否させた（817.5s で失敗）。
`versions.tf` が `prevent_deletion_if_contains_resources = true` を明示しており
（コメント: 「残留を静かに握り潰すと `check_residual.py` の一次データが歪む」）、
**IaC 管理外の自動生成リソースを検知して止まった**。`false` にすれば1回で終わるが、
それは今回見つけた残留を隠すことになる。

最終状態: `mcml-dev-rg` 削除完了。残るのは state 用の `mcml-tfstate-rg`
（ブートストラップ資源・Terraform 管理外・実質無料）のみ。**課金は止まっている。**

**⑦ のツール側の穴（未修正・5基盤共通）**: `check_residual.py` は RG 消滅後に列挙できず
`ResourceGroupNotFound` を **ERROR** として返し、「残留 0 件」と区別がつかない。
完全撤収後の実行が想定されていない。今回の実測は `az group list` /
`az keyvault list-deleted` で手動確認した。

## 比較軸への追加提案

Vertex は 17 リソースが1回で通り、Azure は 10 リソースで**人手の契約変更ゲートに2回**当たった。
この非対称性は IAM friction とは別の軸として立てる価値がある。

| 追加軸 | 測るもの | Vertex | Azure ML |
|---|---|---|---|
| approval friction | IaC だけで到達できない境界があるか | 無し（ADC + API 有効化のみ） | **有り**（offer とプランの変更が前提・2枚） |
| 失敗時の冪等性 | 失敗が state と実体を乖離させるか | 乖離なし | **孤児が残り再実行を塞ぐ** |
| ステージ再開性 | 中断後に途中段から再開できるか | 可（`AIP_MODEL_DIR`） | **不可**（train からやり直し） |
| 撤収の完全性 | destroy 1回で消えるか | 消える | **IaC 管理外の自動生成が残る** |

いずれも運用設計に直接効く（撤退手順の先頭に「孤児の確認と削除」が要るか、
再実行コストが1ステージか全ステージか、撤収に手作業が要るか）。

## 付録: 無料試用版で停止した時点の記録（書き換えない）

契約変更というゲートに当たった事実自体が計測値なので、停止時点の表をそのまま残す。

| # | 項目 | 当時の結果 |
|---|---|---|
| ① | terraform apply | ❌ **9件中8件のみ**。compute cluster が quota で失敗（3回） |
| ②〜⑤ | 学習 / 到達 / 登録 / 推論 | 未達（compute が無く着手不能） |
| ⑥ | terraform destroy | △ 実体は全削除。RG 削除が terraform でタイムアウトし `az group delete` で完了 |
| ⑦ | 残留リソース記録 | 未実施（②〜⑤未達のため意味を成さない） |
| ⑧ | 比較レポート | ✅ **停止した事実**を記録 |

| 指標 | 当時の値 |
|---|---|
| apply 試行回数 | **3回とも失敗** |
| apply 所要 | 260.9s / 13.1s / 35.4s |
| 作成できたリソース | 8/9（compute cluster 以外） |
| destroy 所要 | 760.9s（失敗）→ `az group delete` で完了 |
| 課金 | **¥0**（クレジット消費なし） |

> ⚠️ **`infra_events` の 2026-08-01 05:27:15 UTC（`apply` / `failure` / 14.7s）は集計から除外する。**
> Azure 起因ではなく、**TTY の無い環境で `make tf-apply` を実行した結果**
> `terraform apply` の対話確認が `EOF` で落ちたもの（`run_terraform.py` は意図的に
> `-auto-approve` を付けず、人が `yes` を打つ設計）。
> **「失敗を消さない」原則に従って行は残すが、上表の「apply 試行回数 3回」はこの1件を含まない。**
> 同じ取り違えを防ぐため、runbook §2 に「apply は対話端末で実行する」を追記済み。
