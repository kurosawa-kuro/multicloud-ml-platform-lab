"""計測3テーブル（ml_runs / infra_events / cost_snapshots）への書き込み（Neon 実装）。

core.telemetry.tracking の RunSink Protocol を実装する。psycopg を使うため
**core からは import しない**（Snowflake warehouse に psycopg3 は無い）。
psycopg が使えない環境では core.telemetry.sinks.JsonlRunSink を使い、
`make collect` で後からここ経由の INSERT に合流する。

接続は platforms.neon.connection（pooled / cold start retry / prepare 無効）。
短命ジョブからの書き込みなので開いて即閉じ、学習本体とトランザクションを
共有しない（学習失敗時にも記録が残る必要がある）。
"""

from __future__ import annotations

import json
from typing import Any

from core.telemetry.schemas import CostSnapshot, InfraEvent, MlRun, Platform, Stage, WritePath
from platforms.neon.connection import Connector
from platforms.neon.connection import connect as default_connect

_INSERT_SQL = """
insert into ml_runs (
    run_id, platform, tier, unification_unit, stage, status, attempt,
    duration_seconds, failure_class, error_excerpt, code_revision,
    write_path, metrics, params
) values (
    %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s::jsonb, %s::jsonb
)
on conflict (run_id) do nothing
"""


# 既存行の params だけを足す。**INSERT は絶対にしない**。
#
# 学習の成功行はジョブ側が書く規約なので、adapter がその行に値を足したいとき
# `insert ... on conflict do nothing` では何も起きない（既に行があるため）。
# かといって upsert にすると、**ジョブが Neon へ届かなかった場合に adapter が
# 行を作ってしまい `write_path='direct'` を騙る**（オーケストレータの egress を
# ジョブの egress と偽る = このラボの比較軸が壊れる）。
# だから「行があれば params を足す / 無ければ何もしない」に限定する。
_MERGE_PARAMS_SQL = """
update ml_runs
   set params = coalesce(params, '{}'::jsonb) || %s::jsonb
 where run_id = %s
"""


_INSERT_INFRA_EVENT_SQL = """
insert into infra_events (
    event_id, platform, action, status, duration_seconds, resource_count, residual_resources
) values (
    %s, %s, %s, %s, %s, %s, %s::jsonb
)
on conflict (event_id) do nothing
"""

_INSERT_COST_SQL = """
insert into cost_snapshots (platform, usage_date, service, amount_usd)
values (%s, %s, %s, %s)
on conflict (platform, usage_date, service) do update set amount_usd = excluded.amount_usd
"""


class NeonRunSink:
    """1 run = 1 INSERT。write_path='direct' を確定させる。

    接続は **コンストラクタで注入**する（既定は実 Neon 接続）。
    差し替え口が無いと、記録層が実 DB 無しで1行も検証できない。
    """

    def __init__(self, connect: Connector = default_connect) -> None:
        self._connect = connect

    def record_run(self, run: MlRun) -> WritePath:
        run.write_path = WritePath.DIRECT
        self.insert_run(run)
        return WritePath.DIRECT

    def insert_run(self, run: MlRun, *, write_path: WritePath | None = None) -> int:
        """collect（JSONL 回収）とも共用する INSERT。戻り値は挿入行数（重複は 0）。"""
        effective = write_path or run.write_path
        with self._connect() as conn:
            cur = conn.execute(
                _INSERT_SQL,
                (
                    run.run_id,
                    run.platform.value,
                    run.tier.value,
                    run.unification_unit.value,
                    run.stage.value,
                    run.status.value,
                    run.attempt,
                    run.duration_seconds,
                    run.failure_class.value if run.failure_class else None,
                    run.error_excerpt,
                    run.code_revision,
                    effective.value,
                    json.dumps(run.metrics, ensure_ascii=False),
                    json.dumps(run.params, ensure_ascii=False),
                ),
            )
            return cur.rowcount

    def merge_run_params(self, run_id: str, params: dict[str, Any]) -> int:
        """既存 run 行の params へ追記する。戻り値は更新行数（不在なら 0）。

        用途は「ジョブが書いた学習成功行に、adapter しか知らない値
        （`model_artifact_uri` など）を足す」こと。これが無いと stage を跨いだ
        再開ができず、中断のたびに train からやり直しになる。

        **0 を返すのは異常ではない。** collected 経路（Tier B・Neon 到達不能）では
        行が `make collect` の後に現れるため、この時点ではまだ存在しない。
        """
        if not params:
            return 0
        with self._connect() as conn:
            cur = conn.execute(
                _MERGE_PARAMS_SQL, (json.dumps(params, ensure_ascii=False, default=str), run_id)
            )
            return cur.rowcount

    def record_infra_event(self, event: InfraEvent) -> int:
        """infra_events へ1行。**残留リソースの一次データ**（scripts/check_residual.py）。

        run と分けているのは、コストと同じく「実行時に確定しない」ため
        （docs/02_architecture.md「計測データ層」）。
        """
        with self._connect() as conn:
            cur = conn.execute(
                _INSERT_INFRA_EVENT_SQL,
                (
                    event.event_id,
                    event.platform.value,
                    event.action.value,
                    event.status.value,
                    event.duration_seconds,
                    event.resource_count,
                    json.dumps(event.residual_resources, ensure_ascii=False),
                ),
            )
            return cur.rowcount

    def record_cost_snapshot(self, snapshot: CostSnapshot) -> int:
        """cost_snapshots へ1行。請求反映に1〜2日遅れるので後追いで入る。"""
        with self._connect() as conn:
            cur = conn.execute(
                _INSERT_COST_SQL,
                (
                    snapshot.platform.value,
                    snapshot.usage_date,
                    snapshot.service,
                    snapshot.amount_usd,
                ),
            )
            return cur.rowcount

    def next_attempt(self, platform: Platform, stage: Stage) -> int:
        """同一 (platform, stage) の既存 run 数 + 1。

        collected も direct も数える（fallback で書いた失敗も試行は試行）。
        """
        with self._connect() as conn:
            row = conn.execute(
                "select count(*) from ml_runs where platform = %s and stage = %s",
                (platform.value, stage.value),
            ).fetchone()
        if row is None:  # pragma: no cover - count(*) は必ず1行返る
            raise RuntimeError("count(*) が行を返さなかった")
        return int(row[0]) + 1
