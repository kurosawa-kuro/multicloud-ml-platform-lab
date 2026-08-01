# Databricks Phase 3 着手前確認

> ✅ **消化済み（2026-08-01）**。該当 Phase は完走し、実測は `docs/comparison/` が正本。
> このファイルは着手前に何を潰したかの記録として残す。

Weight Class: Light（調査のみ）

## Goal

Phase 3（Databricks）開始前に次を確認する。**環境は Free Edition のワークスペース**
（2026-08-01 作成）で、実装済み Terraform module がそのまま通るかは未検証。

1. 無償枠・トライアルの範囲と期限。**あわせて PAT の有効期限上限**
   （SP を発行できないため PAT が唯一の経路。期限が Phase 3 の実行可能期間を決める）
2. serverless compute から外部 PostgreSQL（Neon）への到達性（egress 制約含む）
3. Terraform provider の Unity Catalog リソース対応範囲
4. **wheel のトップレベルパッケージ名 `core` が Databricks Runtime / serverless 環境と衝突しないか**（実機で `python -c "import core; print(core.__file__)"` を wheel インストール前後で確認）。汎用名のため理論上の衝突リスクがあり、改名は5基盤共通の import 契約変更 = 大改修になるので、**衝突が実測された場合のみ** owner と改名を協議する（owner 承認 2026-07-31: 現状据え置き）

### Free Edition で apply が通るか（2026-08-01 追加）

Free Edition は serverless 専用。実装済み module に**クラシック compute 前提のリソースと、
上位プラン前提かもしれないリソース**が含まれる。apply してから気付くと再実行になるので先に確認する。

| # | 対象 | 確認すること | 通らなかった場合 |
|---|---|---|---|
| 5 | `databricks_cluster_policy`（[modules/databricks/main.tf:142](../../../infra/modules/databricks/main.tf)） | Free Edition でクラスタポリシーを作れるか | `count` で外す。ジョブは serverless なので実害なし（「クラシック使用時の保険」でしかない） |
| 6 | `databricks_model_serving`（同 :199） | **Free Edition で Model Serving を作れるか** | **完了条件⑤（1件推論）が成立しない = Phase 3 の go/no-go に直結。** 代替を実装せず、使えない事実を比較結果として記録する |
| 7 | `create_catalog` | UC メタストアに対し `CREATE CATALOG` 権限があるか | `TF_VAR_create_catalog=false` で既存カタログを使う |
| 8 | `system.billing.usage` | Free Edition から system tables を参照できるか | `collect-costs` が Databricks だけ空になる。**0円で埋めない**（取得不能として記録） |

## Value

いずれも仕様変更が入りやすい領域で、推測で設計すると手戻りする。特に 2 は「Neon 到達可否自体が比較軸」という設計の前提。

## Scope

- 上記1〜8の確認と記録（5〜8 は `terraform plan` / 実 apply で判定してよい）

## Non-scope

- Databricks インフラ・adapter の実装
- Free Edition の制約を回避するための代替実装（使えないことは比較結果として記録する）

## Done

- 1〜8 の確認結果（参照ドキュメントの日付/バージョン付き）が本 task に記録され、Phase 3 の設計に反映されている
- Neon 直接到達が不可の場合、fallback（JSONL + `make collect`）前提で計画されている
- **項目6の結論が出ている**（Model Serving 不可なら Phase 3 の完了条件を8項目のまま通せないので、
  go/no-go を owner に上げる）

## Evidence

- Databricks 公式ドキュメント / Terraform provider ドキュメント（参照日明記）

## Stop / Ask Owner If

- **期限**: Phase 3 開始時に必ず実施（それまで着手不要）。

## 出典

- [../../archive/managed-ml-platform-comparison-brainstorm-v2.md](../../archive/managed-ml-platform-comparison-brainstorm-v2.md) §6・§13

---

## 実測（2026-08-01・ローカルで消化できた分）

クラウドに触らずに確定できるものを先に潰した。**残りは PAT 発行待ち**。

### 配布物（項目 3 の前提・runbook §3 のチェックリスト）

| 確認 | 結果 |
|---|---|
| `make wheel` | ✅ `multicloud_ml_platform_lab-0.1.0-py3-none-any.whl` |
| entry point | ✅ `train = platforms.databricks.job_main:main` |
| dist 名の4箇所一致 | ✅ Terraform 既定 `job_package_name = multicloud_ml_platform_lab` / `wheel_filename` / 実 dist |
| `_stamp.py` 同梱 | ✅ `CODE_REVISION = 843b3a91…` |
| top-level パッケージ | `core` / `platforms`（項目4の衝突確認は実機接続後） |
| wheel の実行時依存 | lightgbm / scikit-learn / pandas / pyarrow のみ。**psycopg は入らない** |

最後の行が項目2の答えの半分になる。**egress 以前に serverless へ psycopg が入らない**ので、
配線を足さない限り `write_path='collected'` は確定（runbook §4「③ の扱い」と一致）。

### SDK の API 実測（databricks-sdk 0.123.0）

adapter が呼ぶメソッドを実物のシグネチャと突き合わせた。**Snowflake で
「クラウドで初めて分かる」を4回やった反省**として、実行前に潰す。

| adapter の呼び出し | 実測 |
|---|---|
| `jobs.run_now(job_id, python_params=[...])` | ✅ 一致 |
| `files.upload(path, contents, overwrite=True)` | ✅ 一致 |
| `serving_endpoints.create_and_wait(name, config=...)` / `update_config_and_wait(name, served_entities=[...])` | ✅ 一致 |
| `serving_endpoints.query(name, dataframe_records=[...])` | ✅ 一致 |
| `model_versions.create(model_name, source, comment)` | ❌ **存在しない**（`delete/get/get_by_alias/list/update` のみ） |

**④ は現在の実装では成立しない。** 公式ドキュメントも
「Creating new model versions requires use of the MLflow Python client」
（[Manage model lifecycle in Unity Catalog](https://docs.databricks.com/aws/en/machine-learning/manage-model-lifecycle/) 参照 2026-08-01）と明記しており、
REST/SDK に版作成の口が無い。加えて **UC の版は model signature 必須**
（`input_example` を渡して推論するのが標準）。

つまり Databricks の④は「artifact URI を渡す」では通らず、
**MLflow 形式のモデル（署名付き）を作る工程が要る**。Snowflake の
「登録が復元済みモデル＋入力サンプル＋依存解決を要求する」と同じ性質で、
**Tier B 共通の構造差**として比較レポートに書ける材料になる。

方式は owner 判断（→ runbook §0）。

### 資格情報

`doppler secrets` 実測: **`DATABRICKS_HOST` / `DATABRICKS_TOKEN` は未登録**
（`DWH_DATABRICKS_TOKEN` は別文脈で流用しない）。①以降すべてこの2件待ち。

### 検査側の穴（runbook §7 の既知の穴）→ 修正済み

`check_residual.check_databricks` の Volume 列挙が `catalog_name="" / schema_name=""` で、
実 API では成立しない（テストは注入クライアントで通っていた）。
`artifacts/dbx-dev.outputs.json` の catalog / schema で引くよう修正し、
**カタログごと消えている場合は「残留ゼロ」、outputs が無い場合は ERROR** に分けた。
あわせて Snowflake 側に未適用だった `LAB_NAME_PREFIX` 絞り込みを入れた
（大小文字を無視。Snowflake は識別子が大文字なので素朴な `in` では一致しなかった）。

### 残（実機接続後にしか測れない）

項目 1（無償枠・PAT 期限）/ 2 の後半（egress 実測）/ 4（`core` 名衝突）/
5（cluster policy）/ 6（**Model Serving の可否 = go/no-go**）/ 7（CREATE CATALOG）/ 8（system tables）

### 項目1・2・6 の一次回答（ドキュメント調査 2026-08-01）

[Free Edition limitations](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations)（doc 更新 2026-07-20）より。詳細は runbook §0-a。

- **項目6（go/no-go）= go。** Model Serving は Free Edition でも作れる。禁止は
  GPU・provisioned throughput・**バッチ推論**・特定モデルで、**CPU のカスタムモデルは
  対象外の記載が無い**。ただし「active endpoint 数の上限」があるため⑤の後は必ず teardown。
- **項目2の前半 = 制限あり（要実測）。** 「outbound internet access is restricted to a
  limited set of trusted domains」。Neon 到達に効くだけでなく、**PyPI が trusted domains に
  入っているか**が②の依存インストール成否を決める。Snowflake と同型の地雷。
- **項目1 = 期限は無いが quota はある。** fair usage を超えるとその日（最悪その月）
  compute が停止。PAT 期限のみ owner が発行時に控える。
- **新規発見: `Verify identity`（LinkedIn 認証）で outbound internet access が解放される。**
  ③ の `write_path` が**アカウントの認証状態に依存する**ため、どちらの状態で測ったかを
  comparison ページに書かないと再現できない。

### ワークスペース実測（2026-08-01・PAT 発行後）

`current_user.me()` 疎通 OK（`<owner-email>`）。read-only プローブの結果:

| 対象 | 実測 | 意味 |
|---|---|---|
| catalogs | `system` / `samples` / `workspace` | ユーザーカタログは未作成。**項目7 は apply で判定** |
| warehouses | `Serverless Starter Warehouse`（2X-Small / STOPPED）1台 | Free Edition の記載どおり |
| serving_endpoints | **基盤提供の基盤モデル 11 本が既に存在** | 「active endpoint 数の上限」に効く可能性。⑤ の teardown を飛ばさない |
| registered_models / jobs | 0 件 | まっさら |
| cluster_policies | 既定5本を列挙可（作成可否は未判定） | **項目5 は apply で判定** |
| `system.billing.usage` | メタデータ取得 OK（MANAGED） | **項目8 は見込みあり**（クエリは warehouse 起動が要る） |
| MLflow experiments | API 応答 OK | ④ の MLflow 経路は塞がれていない |

**副次的な確認**: 既存の 11 本は名前に `mcml` を含まないため、今回入れた
`LAB_NAME_PREFIX` 絞り込みで残留検査から除外される。**絞り込みが無ければ
destroy 後に毎回 FAIL 11 件**（＝嘘の赤）になっていた。

### ① の plan 実測

```
TF_VAR_job_principal="<owner email>" make ENV=dbx-dev tf-plan
→ Plan: 10 to add, 0 to change, 0 to destroy / grants_enabled = true
   catalog=mcml_dev / schema=ml / volume=/Volumes/mcml_dev/ml/artifacts
   model=mcml_dev.ml.california_housing
```

provider v1.123.0 は workspace-level の env 認証で通った（Snowflake の provider/connector
形式差のような罠は無し）。serving は2段階目なのでこの plan には含まれない（想定どおり）。

### 項目5・7 の結論（apply 実測 2026-08-01）

- **項目5（cluster policy）= 作れる。** Free Edition でも
  `databricks_cluster_policy` は作成成功（id 取得済み）。回避策は不要だった。
- **項目7（CREATE CATALOG）= 作れない。** `Metastore storage root URL does not exist.
  Default Storage is enabled in your account.` **SDK 直呼びでも同じエラー**なので
  provider の問題ではなくアカウント側の制約。既存カタログ `workspace` に相乗りし、
  **スキーマ名にラボ接頭辞**（`mcml_dev`）を入れて残留検査の絞り込みを効かせた。
  - この結果、Terraform 網羅度の比較で **Databricks は「カタログを作れない」**という
    Free Edition 固有の欠落が1つ入る（有料版では作れる想定なので、その旨も併記する）。
