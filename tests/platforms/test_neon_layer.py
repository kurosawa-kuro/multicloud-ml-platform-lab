"""Neon 層（計測到達点）の検証。**実 DB を立てずに接続を注入して回す。**

この層は「全基盤の計測が集まる先」（docs/02_architecture.md）なのに、
接続がモジュール直呼びで DI の継ぎ目が無く、**テストが1件も無かった**。
`Connector` を引数で受ける形にして、ここで固定する。

守る不変条件:
  - ml_runs は write_path='direct' を **sink 側で確定**させる（JSONL は collected）
  - infra_events / cost_snapshots は run と別テーブルへ入る（実行時に確定しないため）
  - cost_snapshots は同一キーで上書き（請求は後から確定値に変わる）
  - attempt は既存 run 数 + 1
  - DDL は **direct endpoint**（pooled では `SET` 等が通らない）
  - 一括投入は COPY → 一時表 → `on conflict do nothing`（再ロードの冪等性）
"""

from __future__ import annotations

from tests.fakes.neon import FakeConnection, FakeConnector
from tests.fakes.telemetry import make_housing_record, make_run

from core.telemetry.schemas import (
    CostSnapshot,
    InfraAction,
    InfraEvent,
    Platform,
    Stage,
    Status,
    WritePath,
)
from platforms.neon import repository, schema
from platforms.neon.run_sink import NeonRunSink

# --- ml_runs --------------------------------------------------------------


def test_record_run_forces_direct_write_path() -> None:
    """Neon へ直接書けた行は direct。JSONL 経由（collected）と混ぜない。"""
    connector = FakeConnector()
    sink = NeonRunSink(connect=connector)
    run = make_run(write_path=WritePath.COLLECTED)

    assert sink.record_run(run) is WritePath.DIRECT
    assert run.write_path is WritePath.DIRECT
    sql, params = connector.connection.statements[0]
    assert sql.startswith("insert into ml_runs")
    assert "on conflict (run_id) do nothing" in sql
    assert params[0] == run.run_id
    assert params[11] == WritePath.DIRECT.value


def test_insert_run_serializes_metrics_and_params_as_json() -> None:
    connector = FakeConnector()
    NeonRunSink(connect=connector).insert_run(make_run())

    _, params = connector.connection.statements[0]
    assert params[12] == '{"rmse": 0.4368}'
    assert params[13] == '{"num_leaves": 31}'


def test_insert_run_can_override_write_path_for_collect() -> None:
    """`make collect` は JSONL 由来なので collected のまま入れる。"""
    connector = FakeConnector()
    NeonRunSink(connect=connector).insert_run(make_run(), write_path=WritePath.COLLECTED)

    _, params = connector.connection.statements[0]
    assert params[11] == WritePath.COLLECTED.value


def test_next_attempt_counts_existing_runs() -> None:
    connection = FakeConnection(results=[[(2,)]])
    sink = NeonRunSink(connect=FakeConnector(connection))

    assert sink.next_attempt(Platform.VERTEX, Stage.TRAIN) == 3
    sql, params = connection.statements[0]
    assert sql.startswith("select count(*) from ml_runs")
    assert params == ("vertex", "train")


# --- infra_events / cost_snapshots ---------------------------------------


def test_infra_event_goes_to_its_own_table() -> None:
    connector = FakeConnector()
    NeonRunSink(connect=connector).record_infra_event(
        InfraEvent(
            event_id="e-1",
            platform=Platform.SNOWFLAKE,
            action=InfraAction.DESTROY,
            status=Status.SUCCESS,
            residual_resources={"findings": [{"kind": "fail_safe"}]},
        )
    )

    sql, params = connector.connection.statements[0]
    assert sql.startswith("insert into infra_events")
    assert params[2] == InfraAction.DESTROY.value
    assert '"fail_safe"' in params[6]


def test_cost_snapshot_upserts_on_the_natural_key() -> None:
    """請求は後から確定値に変わるので、同じキーは上書きする。"""
    connector = FakeConnector()
    NeonRunSink(connect=connector).record_cost_snapshot(
        CostSnapshot(
            platform=Platform.SAGEMAKER,
            usage_date="2026-07-30",
            service="Amazon SageMaker",
            amount_usd=1.25,
        )
    )

    sql, params = connector.connection.statements[0]
    assert sql.startswith("insert into cost_snapshots")
    assert "on conflict (platform, usage_date, service) do update" in sql
    assert params == ("sagemaker", "2026-07-30", "Amazon SageMaker", 1.25)


# --- 学習データ（california_housing）------------------------------------


def test_insert_many_uses_copy_into_staging_then_upserts() -> None:
    """20640 行を executemany で入れない。再ロードは冪等。"""
    connection = FakeConnection(rowcounts=[0, 2])
    connector = FakeConnector(connection)

    inserted = repository.insert_many(
        [make_housing_record(1), make_housing_record(2)], connect=connector
    )

    texts = connection.sql_texts()
    assert any(t.startswith("create temp table") and "on commit drop" in t for t in texts)
    assert any(t.startswith("copy california_housing_staging") for t in texts)
    assert any("on conflict (row_id) do nothing" in t for t in texts)
    assert len(connection.copied_rows) == 2
    assert inserted == 2


def test_insert_many_with_no_records_touches_no_connection() -> None:
    connector = FakeConnector()

    assert repository.insert_many([], connect=connector) == 0
    assert connector.calls == []


def test_fetch_all_orders_by_row_id() -> None:
    """順序を固定しないと分割の再現性が壊れる = metric parity が崩れる。"""
    connection = FakeConnection(results=[[make_housing_record(1).as_row()]])

    rows = repository.fetch_all(limit=10, offset=5, connect=FakeConnector(connection))

    sql, params = connection.statements[0]
    assert "order by row_id" in sql
    assert sql.endswith("limit %s offset %s")
    assert params == [10, 5]
    assert rows[0].row_id == 1


def test_count_reads_the_table() -> None:
    connection = FakeConnection(results=[[(20640,)]])

    assert repository.count(connect=FakeConnector(connection)) == 20640


def test_summary_returns_distribution_keys() -> None:
    connection = FakeConnection(results=[[(20640, 2.06, 0.14, 5.0, 1.15)]])

    result = repository.summary(connect=FakeConnector(connection))

    assert set(result) == {"rows", "mean", "min", "max", "std"}
    assert result["rows"] == 20640


# --- DDL は direct endpoint ----------------------------------------------


def test_ddl_uses_direct_endpoint() -> None:
    """pooled（transaction mode）では DDL 系が通らない。"""
    connector = FakeConnector()

    schema.create_table(connect=connector)

    assert connector.calls == [{"direct": True}]
    assert connector.connection.sql_texts()[0].startswith("create table if not exists")


def test_drop_table_also_uses_direct_endpoint() -> None:
    connector = FakeConnector()

    schema.drop_table(connect=connector)

    assert connector.calls == [{"direct": True}]
