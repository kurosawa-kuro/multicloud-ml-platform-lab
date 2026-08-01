"""計測レコードのテストファクトリと in-memory sink。

MlRun / HousingRecord の生成が3ファイルに散らばっていたのを1箇所へ。
フィールドを増やしたときの追従点をここだけにする（散らばっていると
スキーマ変更のたびに全ファクトリを探して回ることになる）。
"""

from __future__ import annotations

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
from core.telemetry.tracking import PLATFORM_TIER
from platforms.neon.records import HousingRecord


def make_run(**overrides: object) -> MlRun:
    """契約を満たす MlRun。platform を変えると tier / unit は自動で追従する。"""
    platform = overrides.pop("platform", Platform.VERTEX)
    assert isinstance(platform, Platform)
    tier, unit = PLATFORM_TIER[platform]
    values: dict[str, object] = {
        "run_id": "00000000-0000-0000-0000-000000000001",
        "platform": platform,
        "tier": tier,
        "unification_unit": unit,
        "stage": Stage.TRAIN,
        "status": Status.SUCCESS,
        "code_revision": "a" * 40,
        "write_path": WritePath.DIRECT,
        "failure_class": FailureClass.NONE,
        "metrics": {"rmse": 0.4368},
        "params": {"num_leaves": 31},
    }
    values.update(overrides)
    return MlRun(**values)  # type: ignore[arg-type]


def make_housing_record(row_id: int = 0) -> HousingRecord:
    """california_housing 1行（実データの先頭行の値）。"""
    return HousingRecord(
        row_id=row_id,
        med_inc=8.3252,
        house_age=41.0,
        ave_rooms=6.984,
        ave_bedrms=1.023,
        population=322.0,
        ave_occup=2.555,
        latitude=37.88,
        longitude=-122.23,
        med_house_val=4.526,
    )


class InMemorySink:
    """RunSink の in-memory 実装。

    JSONL を経由せず「track() が何を記録しようとしたか」だけを見たいテスト用
    （tracking の単体テストと、記録失敗の注入に使う）。
    """

    def __init__(self) -> None:
        self.runs: list[MlRun] = []
        self.attempts: dict[tuple[Platform, Stage], int] = {}

    def record_run(self, run: MlRun) -> WritePath:
        self.runs.append(run)
        return WritePath.DIRECT

    def next_attempt(self, platform: Platform, stage: Stage) -> int:
        key = (platform, stage)
        self.attempts[key] = self.attempts.get(key, 0) + 1
        return self.attempts[key]


__all__ = [
    "FailureClass",
    "InMemorySink",
    "MlRun",
    "Platform",
    "Stage",
    "Status",
    "Tier",
    "UnificationUnit",
    "WritePath",
    "make_housing_record",
    "make_run",
]
