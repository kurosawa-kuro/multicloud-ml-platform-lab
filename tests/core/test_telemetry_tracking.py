"""失敗も必ず記録されることの契約。

permission friction（最小権限で通るまで何回直したか）が本プロジェクトの本命。
失敗 run が落ちる実装だとこの指標が原理的に出せないため、機械で守る。
"""

from __future__ import annotations

import pytest
from tests.fakes.telemetry import InMemorySink

from core.telemetry.schemas import FailureClass, MlRun, Platform, Stage, Status, Tier, WritePath
from core.telemetry.tracking import classify_failure, track

FakeSink = InMemorySink  # 記録先のスタブ（fakes/telemetry.py に集約）


@pytest.fixture
def sink() -> InMemorySink:
    return InMemorySink()


def test_success_is_recorded(sink: InMemorySink) -> None:
    with track(Platform.VERTEX, Stage.TRAIN, sink=sink) as run:
        run.metrics = {"rmse": 0.43}

    assert len(sink.runs) == 1
    recorded = sink.runs[0]
    assert recorded.status is Status.SUCCESS
    assert recorded.failure_class is FailureClass.NONE
    assert recorded.metrics == {"rmse": 0.43}
    assert recorded.code_revision


def test_failure_is_recorded_and_reraised(sink: InMemorySink) -> None:
    """例外は握り潰さず、それでも記録は残ること。"""
    with pytest.raises(PermissionError):  # noqa: PT012 - track の副作用を検証する
        with track(Platform.SAGEMAKER, Stage.TRAIN, sink=sink):
            raise PermissionError(
                "AccessDenied: not authorized to perform sagemaker:CreateTrainingJob"
            )

    assert len(sink.runs) == 1
    recorded = sink.runs[0]
    assert recorded.status is Status.FAILURE
    assert recorded.failure_class is FailureClass.IAM
    assert "AccessDenied" in (recorded.error_excerpt or "")


def test_attempt_increments_across_retries(sink: InMemorySink) -> None:
    """同じ stage を直しながら再試行した回数が数えられること。

    これが permission friction そのもの。
    """
    for _ in range(3):
        with pytest.raises(PermissionError):  # noqa: PT012
            with track(Platform.VERTEX, Stage.TRAIN, sink=sink):
                raise PermissionError("permission denied")

    with track(Platform.VERTEX, Stage.TRAIN, sink=sink):
        pass

    assert [r.attempt for r in sink.runs] == [1, 2, 3, 4]
    assert [r.status for r in sink.runs][-1] is Status.SUCCESS


def test_tier_is_derived_from_platform(sink: InMemorySink) -> None:
    """Tier を adapter 側で手書きさせない（値がばらつくと tier 別集計が壊れる）。"""
    with track(Platform.SNOWFLAKE, Stage.TRAIN, sink=sink):
        pass
    assert sink.runs[0].tier is Tier.B

    with track(Platform.VERTEX, Stage.TRAIN, sink=sink):
        pass
    assert sink.runs[1].tier is Tier.A


def test_recording_failure_does_not_break_the_run(sink: InMemorySink) -> None:
    """記録に失敗しても本処理は通ること（記録の失敗で試行を失わない）。"""

    class BrokenSink(FakeSink):
        def record_run(self, run: MlRun) -> WritePath:
            raise RuntimeError("neon unreachable")

    broken = BrokenSink()
    with track(Platform.DATABRICKS, Stage.TRAIN, sink=broken) as run:
        run.metrics = {"rmse": 1.0}
    # 例外が漏れないことが検証対象


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("AccessDenied: user is not authorized", FailureClass.IAM),
        ("ResourceExhausted: quota exceeded for CustomJob", FailureClass.QUOTA),
        ("connection timeout to pooler.neon.tech", FailureClass.NETWORK),
        ("ModuleNotFoundError: no module named lightgbm", FailureClass.PACKAGE),
        ("manifest unknown: image not found", FailureClass.CONTAINER),
        ("column med_inc missing from schema", FailureClass.DATA),
        ("something entirely unexpected", FailureClass.SDK),
    ],
)
def test_classify_failure(message: str, expected: FailureClass) -> None:
    assert classify_failure(RuntimeError(message)) is expected


def test_unclassified_failure_never_returns_none() -> None:
    """未分類のまま記録しない。空だと permission friction の分母が壊れる。"""
    assert classify_failure(Exception("")) is FailureClass.SDK
