# 05 データモデル

> DDL 正本は `sql/schema.sql`（実装済み。権威順位: コード > docs。本文書は設計意図の説明）。出典: [archive/managed-ml-platform-comparison-brainstorm-v2.md](./archive/managed-ml-platform-comparison-brainstorm-v2.md) §7 から蒸留。

## 設定

| 種別 | 方針 |
|---|---|
| 一般設定 | `env/config.yaml` または環境変数（YAML defaults → ENV override の2段） |
| ローカル秘密情報 | `env/secret.yaml`（ignore 済み・コミット禁止） |
| 共有 / 本番の秘密情報 | Doppler（`doppler.yaml`。値は書かずキー名と用途のみ管理） |

## データ配置

| データ | 置き場所 | 備考 |
|---|---|---|
| 入力データ | `fetch_california_housing` → **Parquet 化して各基盤のストレージへ配置**（GCS / S3 / Blob / UC Volume / stage） | fixture。sklearn 版のみ（Kaggle 版混入禁止 → snowflake-phase-precheck） |
| モデル成果物 | 各基盤の artifact 置き場: `model.txt` / `metrics.json` / `feature_importance.csv` / `run.json` | run.json = run 同定 manifest（run_id / code_revision / metrics / artifact_uri） |
| 計測データ（正本） | **Neon PostgreSQL**（下記3テーブル） | 全基盤の到達点。SELECT だけで比較できる状態を保つ |
| fallback 計測データ | 各基盤ストレージ上の JSONL → `make collect` で Neon へ流し込み | 使った事実を `write_path='collected'` で記録 |

Neon 接続: 書き込み = pooled endpoint（PgBouncer transaction mode・NullPool・prepared statements 無効・suspend 明け retry）、DDL/migration = direct endpoint。詳細は [02_architecture.md](./02_architecture.md) の Neon 集約節。Neon project を既存2 project に相乗りするか3つ目にするかは owner 判断待ち。

## 計測スキーマ（3テーブル）

run 単位 / インフラ操作単位 / コストを分離する。混在させると NULL が増え、コストは請求反映に1〜2日遅れるため実行時に確定しない。

```sql
create table ml_runs (
    run_id uuid primary key,
    platform text not null,        -- vertex | sagemaker | azureml | databricks | snowflake
    tier text not null,            -- A | B
    unification_unit text not null,-- container | package
    stage text not null,           -- train | register | deploy | predict
    status text not null,          -- success | failure
    attempt int not null default 1,
    duration_seconds double precision,
    failure_class text,            -- iam | quota | container | package | network | sdk | data | none
    error_excerpt text,
    code_revision text not null,   -- git sha of src/core/ml
    write_path text not null,      -- direct | collected
    metrics jsonb,
    params jsonb,
    created_at timestamptz not null default now()
);

create table infra_events (
    event_id uuid primary key,
    platform text not null,
    action text not null,          -- apply | destroy
    duration_seconds double precision,
    resource_count int,
    residual_resources jsonb,      -- what survived destroy
    status text not null,
    created_at timestamptz not null default now()
);

create table cost_snapshots (
    platform text not null,
    usage_date date not null,
    service text not null,
    amount_usd numeric not null,
    primary key (platform, usage_date, service)
);
```

設計上の核心:

- `failure_class` と `attempt` が本命（「最小権限で通るまで何回直したか」）。成功だけ記録するとこの情報が消える。
- `code_revision` は not null。contract test で全基盤一致を検証する。
- `created_at` は UTC（timestamptz）。

## 比較クエリ

定番 SELECT 4本 + 補助1本（metric parity / permission friction / stage 別所要 / teardown 品質 / write_path 内訳）の正本は `sql/comparison_queries.sql`（実装済み。参照整合は `tests/test_sql_contracts.py` が pin）。設計全文は [archive §7](./archive/managed-ml-platform-comparison-brainstorm-v2.md) にある。

## 関連タスク

- schema、migration、設定、永続化方式の変更は task に目的・移行手順・検証方法を残す。
- 破壊的変更や後方互換が絡む変更は、実装前に `docs/tasks/03_active/` で作業計画を固定する。
- 確定した migration 手順は `docs/runbooks/` または `08_release_runbook.md` へ昇格する。
