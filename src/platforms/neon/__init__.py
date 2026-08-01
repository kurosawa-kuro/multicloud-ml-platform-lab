"""Neon PostgreSQL アクセス層 + 学習入力データ（California Housing）。

**connection.py が Neon 接続の唯一の正本。** src/core/telemetry も同モジュールを使う。
接続の作法（pooled / direct の使い分け、PgBouncer transaction mode の制約、
cold start リトライ）を2箇所に書かないための集約点。

src/core/telemetry との責務境界:
    neon      = 学習の入力データ（california_housing テーブル）+ 接続層
    telemetry = 計測データ（ml_runs / infra_events / cost_snapshots）

このデータは、Tier B（Databricks / Snowflake）が「データのある場所で計算」する
経路と、Tier A が Parquet 化して各基盤ストレージへ配る経路の共通の起点になる。

接続は Doppler 管理:
    NEON_MULTICLOUD_POOLED_URI  読み書き（PgBouncer transaction mode）
    NEON_MULTICLOUD_DIRECT_URI  DDL・migration
"""
