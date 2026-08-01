-- 計測スキーマ（Neon PostgreSQL）。
--
-- 設計正本は docs/05_data_model.md。実装後は本ファイルが DDL の正本。
-- 適用は direct endpoint（NEON_MULTICLOUD_DIRECT_URI）で行う。
-- pooled endpoint は PgBouncer transaction mode のため DDL に向かない。
--
-- run 単位 / インフラ操作単位 / コストを3テーブルに分離する。
-- 混在させると NULL が増え、コストは請求反映に1〜2日遅れて実行時に確定しない。

-- 学習・登録・デプロイ・推論の1試行。
-- 失敗した試行も必ず1行入れる。成功だけ記録すると
-- 「最小権限で通るまで何回直したか」（permission friction）が消える。
create table if not exists ml_runs (
    run_id           uuid primary key,
    platform         text not null,  -- vertex | sagemaker | azureml | databricks | snowflake
    tier             text not null,  -- A | B
    unification_unit text not null,  -- container | package
    stage            text not null,  -- train | register | deploy | predict | teardown
    status           text not null,  -- success | failure
    attempt          int  not null default 1,
    duration_seconds double precision,
    failure_class    text,           -- iam | quota | container | package | network | sdk | data | none
    error_excerpt    text,
    code_revision    text not null,  -- 実行時の repo HEAD SHA。基盤ごとに異なってよい
                                     -- （比較の前提は src/core/ml の tree 一致。test_code_revision_parity.py が検証）
    write_path       text not null,  -- direct | collected（Neon への到達経路そのものが比較軸）
    metrics          jsonb,
    params           jsonb,
    created_at       timestamptz not null default now()
);

-- terraform apply / destroy の1操作。
-- residual_resources が「撤退しても消えないもの」の一次データ。
create table if not exists infra_events (
    event_id           uuid primary key,
    platform           text not null,
    action             text not null,  -- apply | destroy
    duration_seconds   double precision,
    resource_count     int,
    residual_resources jsonb,          -- destroy 後に残ったもの
    status             text not null,
    created_at         timestamptz not null default now()
);

-- コストは実行時に確定しないので後追いで入れる。
create table if not exists cost_snapshots (
    platform    text    not null,
    usage_date  date    not null,
    service     text    not null,
    amount_usd  numeric not null,
    primary key (platform, usage_date, service)
);

-- 学習の入力データそのもの。DDL の正本は src/platforms/neon/schema.py（実装済み）。
-- Tier B が「データのある場所で計算」する経路の起点でもある。
-- create table california_housing (...) -- src/platforms/neon/schema.py を参照

create index if not exists ml_runs_platform_stage_idx on ml_runs (platform, stage);
create index if not exists ml_runs_code_revision_idx  on ml_runs (code_revision);
create index if not exists infra_events_platform_idx  on infra_events (platform, action);
