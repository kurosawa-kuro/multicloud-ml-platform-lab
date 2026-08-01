"""記録先（sink）の core 側実装。

**core は Neon ドライバ（psycopg）を知らない。** Snowflake warehouse の
Anaconda channel に psycopg3 は無く、core が DB ドライバへ依存した瞬間に
Tier B で import すら通らなくなる。層の向きは:

    core.telemetry.tracking   … RunSink Protocol（契約）と track()
    core.telemetry.sinks      … JsonlRunSink（stdlib のみ・全基盤で必ず動く）
    platforms.neon.run_sink   … NeonRunSink（psycopg。動く環境でだけ import する）

JSONL は「Neon へ直接届けられない環境」の一次記録。各基盤ストレージへ退避し、
`make collect`（scripts/collect_jsonl.py）がローカルから Neon へ流し込む。
その行の write_path は 'collected' — **到達経路そのものが比較軸**なので、
direct と混ざらないよう sink の時点で確定させる。
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from core.telemetry.schemas import (
    FailureClass,
    MlRun,
    Platform,
    Stage,
    Status,
    Tier,
    UnificationUnit,
    WritePath,
)

JSONL_FILENAME = "ml_runs.jsonl"


def run_to_record(run: MlRun) -> dict[str, Any]:
    """MlRun → JSON 化可能な dict（Enum は値へ）。"""
    record = dataclasses.asdict(run)
    for key, value in record.items():
        if hasattr(value, "value"):
            record[key] = value.value
    record.pop("created_at", None)  # 挿入時刻は DB の default now() に任せる
    return record


def record_to_run(record: dict[str, Any]) -> MlRun:
    """JSONL の1行 → MlRun。collect 側で使う。"""
    return MlRun(
        run_id=record["run_id"],
        platform=Platform(record["platform"]),
        tier=Tier(record["tier"]),
        unification_unit=UnificationUnit(record["unification_unit"]),
        stage=Stage(record["stage"]),
        status=Status(record["status"]),
        code_revision=record["code_revision"],
        write_path=WritePath(record["write_path"]),
        attempt=int(record.get("attempt", 1)),
        duration_seconds=record.get("duration_seconds"),
        failure_class=FailureClass(record["failure_class"])
        if record.get("failure_class")
        else None,
        error_excerpt=record.get("error_excerpt"),
        metrics=record.get("metrics") or {},
        params=record.get("params") or {},
    )


class JsonlRunSink:
    """ローカル JSONL への追記。stdlib のみで、5基盤すべてで必ず動く最後の砦。"""

    def __init__(self, directory: Path) -> None:
        self._path = Path(directory) / JSONL_FILENAME
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def record_run(self, run: MlRun) -> WritePath:
        # この行は collect 経由でしか Neon に届かないため、ここで collected 確定
        run.write_path = WritePath.COLLECTED
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(run_to_record(run), ensure_ascii=False) + "\n")
        return WritePath.COLLECTED

    def next_attempt(self, platform: Platform, stage: Stage) -> int:
        """既存 JSONL を数えて attempt を進める。行数は高々数十なので線形走査で足りる。"""
        if not self._path.exists():
            return 1
        count = 0
        with self._path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                record = json.loads(line)
                if record.get("platform") == platform.value and record.get("stage") == stage.value:
                    count += 1
        return count + 1
