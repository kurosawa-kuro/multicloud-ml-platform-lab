# Vertex AI

> 実測日 2026-08-01 / project `example-gcp-project` / region `us-central1` /
> `code_revision = 35d48cbbb92e8700f78ea54df8fc9495d4d7fff2`
> 実行手順と合否判定: [../runbooks/動作検証-vertex.md](../runbooks/動作検証-vertex.md)

Tier: A（コンテナ実行型・統一単位 = 学習イメージ）

## 構造仮説（着手前の予想・実測で検証する）

- ML 固有リソースの Terraform 対応が限定的（Custom Job / Model Upload / Deploy は SDK に残る）
- Phase 1 のアンカー。既存資産（ML/kaggle-bronze-gcp）が最も厚く、ここが基準線になる

| 予想 | 実測 | 差分 |
|---|---|---|
| ML 固有リソースの IaC 対応は限定的 | **そのとおり**。Terraform は器（Endpoint / SA / IAM / GCS / AR）まで。CustomJob・Model・デプロイは SDK | 予想どおり |
| 撤退時に GCS / Artifact Registry が残る（runbook の期待表は WARN 想定） | **外れ**。バケットも AR も Terraform 管理下で `force_destroy` が効き、成果物ごと消えた | 残るのは *IaC の外で作ったもの* だけ、という規則性が見えた（下記） |
| — （予想していなかった） | **残ったのは Model Registry の登録モデル 1 件。** SDK が作るので `terraform destroy` の対象外、adapter の teardown も Endpoint しか消さない | **「誰が作ったか」が「何が残るか」を決める**。Tier A の残留は所有者境界（Terraform / SDK）にきれいに一致する |
| — （予想していなかった） | **デプロイが突出して遅い（1356s）。** 学習 10s・登録 155s に対し1桁違う | Tier A の律速は学習ではなく**エンドポイントの起動**。アイドル課金だけでなく「立ち上げ待ち」も撤退判断のコストになる |

## 完了条件8項目

| # | 項目 | 結果 |
|---|---|---|
| ① | terraform apply | ✅ 17 resources / 30.4s（infra_events に記録） |
| ② | 学習ジョブ成功（失敗試行も記録済み） | ✅ `JOB_STATE_SUCCEEDED`。attempt=2（1回目は失敗、下記） |
| ③ | Neon へメトリクス到達（direct / collected） | ✅ **direct**（ジョブ内から直接 INSERT。全6行とも direct） |
| ④ | モデル登録 | ✅ `mcml-california-housing` v1 / 154.7s |
| ⑤ | 1件オンライン推論 | ✅ 2.0s / 予測値 `4.183217948107466` |
| ⑥ | terraform destroy | ✅ 16 resources / 20.2s |
| ⑦ | 残留リソース記録 | ✅ **WARN 1件**（登録モデル）／ 課金が続くもの（FAIL）は 0件 |
| ⑧ | 比較レポート1ページ記述（本ページ） | ✅ |

## 実測値

数値は Neon の SELECT（`sql/comparison_queries.sql`）から起こした。手で数えていない。

| 指標 | 値 | 出典 |
|---|---|---|
| RMSE | **0.4368055090296257** | metric parity |
| code_revision | `35d48cbb…`（全 run で1種類） | metric parity |
| 最小権限で通るまでの試行回数 | train **2** / register 1 / deploy 1 / predict 1 | permission friction |
| failure_class の内訳 | `package` × 1（他はゼロ。**iam は0回**） | permission friction |
| stage 別所要時間 | train 10.0s（ジョブ内）/ register 154.7s / **deploy 1356.0s** / predict 2.0s | stage 別所要 |
| Neon 到達経路 | **direct 6 / collected 0** | 到達経路内訳 |
| Terraform でカバーできた範囲 | API 有効化8・SA・IAM 4・actAs 1・GCS・Artifact Registry・**Endpoint の器** = 17 | 手記述 |
| SDK/CLI/SQL に残った範囲 | CustomJob 投入・Model Upload・デプロイ・推論・Endpoint 削除 | 手記述 |
| destroy 後の残留 | **Model Registry の登録モデル 1件**（`mcml-california-housing:1`）。課金継続なし | teardown 品質 |
| アイドル時課金の構造 | デプロイ中のみ課金（`n1-standard-2` × 1）。Endpoint の器だけなら無課金 | 手記述 |

補足: train の所要は**2つの意味**があり、混同すると比較を誤る。

- **10.0s** = ジョブの中で測った学習時間（`ml_runs` に載るのはこちら。ジョブ側が記録者）
- **259.3s** = adapter から見た投入→完了（Spot の待ち + プロビジョニング + イメージ pull を含む）

`ml_runs` は前者を持つので、**基盤間の「学習の速さ」比較には前者を使う**。
後者（待ち時間込み）は基盤の混雑・Spot 供給に依存し、再現しない。

## 詰まった点（一次記録）

失敗の記録がこのプロジェクトの本体。うまくいった手順よりも、何回何を直したかを残す。

| # | 事象 | 分類 | 対処 |
|---|---|---|---|
| 1 | `phase-train` が `ModuleNotFoundError: No module named 'google'` で即失敗 | `package` / attempt 1 | `make deps` は基盤 SDK を入れない（学習コンテナを太らせないため extra 分離）。`make PLATFORM=<p> deps-platform` を新設し runbook §1 のチェックリストへ追加 |
| 2 | **ユニットテストが実 terraform を apply した** | 事故 | `run_terraform(runner=stream_command)` の既定引数が定義時に束縛され、テストの monkeypatch が効かなかった。既定を `None` にして関数内解決へ変更し、`main(runner=…, sink=…)` の注入口を追加。誤って作られた 16 リソースは `--no-record` で巻き戻し、計測は destroy → apply をやり直して取得 |
| 3 | `run_terraform.py` に `-auto-approve` も保存済み plan も渡せない | 実装漏れ | 位置引数 `nargs="*"` だけでは argparse がフラグを弾く。`parse_known_args` に変更（保存済み plan の適用が可能になり、レビューした計画と適用内容が一致する） |
| 4 | `check_residual.py` が `ImportError` で丸ごと落ちた | 実装漏れ | クライアント生成が `guarded()` の外にあり、「検査できなかった」が1行も残らなかった（= 残留ゼロと区別が付かない）。`lazy_clients()` で生成を guarded の内側へ。`google-cloud-artifact-registry` を gcp extra に追加 |
| 5 | 残留検査が**他プロジェクトのバケット12件**を Vertex の残留として報告 | 誤検出 | 共有プロジェクトの全バケットを列挙していた。`LAB_NAME_PREFIX`（`mcml`）で本ラボのリソースだけに絞った。誤検出を記録した `infra_events` の1行は削除（既知の偽値を残すと比較表が嘘になる） |
| 6 | Artifact Registry の検査が常に 400（`RESOURCE_PROJECT_INVALID`） | 実装漏れ | `list_repositories()` に `parent` を渡していなかった。一度も成立していなかった検査 |
| 7 | 残留ゼロだと `infra_events` に1行も残らなかった | 実装漏れ | finding のある基盤だけ記録していた。**検査した基盤は0件でも記録**するよう変更（「撤退できた証拠」が消えていた） |
| 8 | 残留検査の項目に **登録モデルが無かった** | 実装漏れ | Azure ML 側には `registered_model` 検査があるのに Vertex には無く、「残留ゼロ」という嘘の結果を一度出した。`registered_model` を追加 |
| 9 | 検査が **別リージョンを見ていた** | 実装漏れ（最も危険） | `_default_gcp_clients()` が `aiplatform.init()` を呼ばず、`Endpoint.list()` / `Model.list()` が環境変数（`GOOGLE_CLOUD_REGION=asia-northeast1`）由来の別リージョンを列挙していた。**対象リージョンに何が残っていても「ゼロ」と報告される**状態。terraform outputs から project/region を解決して init するよう修正 |

**⑦ は3回書き直している**（8→9→正しい値）。最初の2回は「残留ゼロ」と出ており、
そのまま採用していたら **Vertex の残留比較が丸ごと嘘**になっていた。
`infra_events` に入った誤りの2行は削除済み。
教訓: **緑（残留ゼロ）こそ疑う。** 検査対象・スコープ・リージョンが正しいかを、
「何かが引っかかる状態」を1度作って確かめてから信用する。

**iam の失敗が0回だった**のは、Terraform 側で SA に4ロール（`aiplatform.user` /
`artifactregistry.reader` / `storage.objectAdmin` / `logging.logWriter`）と
投入者への `actAs` を最初から与えていたため。**権限を絞り込む過程を測れていない**ので、
permission friction の値としては「既知の正解を先に置いた場合」の下限とみなす。

## 撤退時に残ったもの

```
$ python scripts/check_residual.py --platform vertex
[WARN] vertex/registered_model: mcml-california-housing:1
-- FAIL/ERROR 0 件 / 全 1 件
```

検査範囲: `vertex_endpoint`（FAIL 判定）/ `gcs_object`（WARN）/
`artifact_registry`（WARN）/ `registered_model`（WARN）。

| 何が | 残ったか | 誰が作ったか |
|---|---|---|
| Vertex Endpoint | ❌ 消えた（課金停止） | Terraform（器）+ SDK（デプロイ） |
| GCS バケットと成果物 | ❌ 消えた（`force_destroy`） | Terraform |
| Artifact Registry とイメージ | ❌ 消えた | Terraform |
| **Model Registry の登録モデル** | ✅ **残った** | **SDK のみ** |

**規則性: Terraform が所有していれば消え、SDK だけで作ったものが残る。**
Vertex の場合それは登録モデル1つに集約される。課金は続かない（デプロイ実体は無い）ので
FAIL ではなく WARN 扱いだが、名前空間は汚れ続けるため、フェーズを繰り返すと版が積み上がる。

### 掃除（測定 → 記録 → 掃除）

残留は**測定して記録した後に消す**。記録が `infra_events` に残っているので、
オブジェクトを残しておく理由が無い（次フェーズの残留測定に前回の残骸が混ざる方が害）。

削除の判断根拠（実測）:

| 観点 | 実測 | 結論 |
|---|---|---|
| 費用 | 課金実体（Endpoint / GCS / AR）は destroy 済み。残るのは Registry のメタデータのみ | 残しても ¥0 |
| 再利用による時間短縮 | `artifactUri` は 404（バケットごと削除）、`containerSpec.imageUri` も NOT_FOUND（AR ごと削除）。**デプロイ不能な空参照** | 短縮ゼロ。仮に生きていても短縮対象は register の 154.7s のみで、律速の deploy 1356s は毎回かかる |
| 比較材料 | `infra_events` に記録済み | 物としては不要 |
| 同一性 | 次フェーズは新しい `code_revision` で学習し直す | 使い回すと同一SHA担保が崩れる |

```bash
gcloud ai models delete <MODEL_ID> --region=us-central1        # 登録モデル
# CustomJob 履歴は gcloud に delete サブコマンドが無い（SDK のみ）
python -c "from google.cloud import aiplatform; aiplatform.init(...); job.delete()"
```

掃除後の再検査は `残留なし / FAIL·ERROR 0件` で、クラウド側の本ラボ資産は
Endpoint・Model・CustomJob・GCS・AR・terraform state すべて 0 件。

**検査していないもの**: CustomJob の実行履歴は「課金されない実行ログ」であり
リソース残留とは性質が違うため、`check_residual.py` の判定項目には**入れていない**
（入れると毎回の実行履歴が残留として並び、比較表が読めなくなる）。
ただし destroy 後も消えずに残る点は Vertex の性質として記録する。
今回は掃除の一環で SDK 経由で削除した。

**apply 17 / destroy 16 の差**が Vertex 固有の記録すべき挙動。Endpoint の器は
Terraform が作ったが、`phase-teardown`（SDK）で先に消したため、destroy 時には
既に存在せず terraform のカウントに乗らなかった。**IaC と SDK が同じリソースを
両方から触れる**ことの副作用で、state と実体の突合を destroy の件数だけで
やってはいけないことを示す（詳細比較は [residual-resources.md](./residual-resources.md)）。
