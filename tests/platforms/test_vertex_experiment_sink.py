"""Vertex AI Experiments 複写（A: 併存）の検証。

**Experiments を実際に叩かない**（SDK を注入する）。ここで守るのは
「複写を足しても計測の正本が動かない」こと。動くと5基盤比較が壊れる。

守る不変条件:
  - `next_attempt` は必ず inner（Neon）へ委譲する（attempt 採番の正本を移さない）
  - `record_run` の戻り値は inner のもの（write_path の判定を横取りしない）
  - Vertex 以外の run は複写しない
  - 複写が失敗しても呼び出し元へ伝えない（telemetry は非致命）
  - `log_params` / `log_metrics` の型制約（float / int / str）を満たす
"""

from __future__ import annotations

from typing import Any

import pytest

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
from platforms.vertex.experiment_sink import (
    VertexExperimentSink,
    experiment_run_name,
    to_metrics,
    to_params,
)


def make_run(
    *,
    platform: Platform = Platform.VERTEX,
    status: Status = Status.SUCCESS,
    params: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
) -> MlRun:
    return MlRun(
        run_id="0d5f7d2e-1111-4222-8333-444444444444",
        platform=platform,
        tier=Tier.A,
        unification_unit=UnificationUnit.CONTAINER,
        stage=Stage.TRAIN,
        status=status,
        code_revision="a" * 40,
        write_path=WritePath.DIRECT,
        attempt=3,
        duration_seconds=12.5,
        failure_class=None if status is Status.SUCCESS else FailureClass.IAM,
        metrics=metrics or {},
        params=params or {},
    )


class SpyInner:
    """inner sink（実体は Neon / JSONL）の代役。"""

    def __init__(self, write_path: WritePath = WritePath.DIRECT) -> None:
        self.runs: list[MlRun] = []
        self.attempt_calls: list[tuple[Platform, Stage]] = []
        self._write_path = write_path

    def record_run(self, run: MlRun) -> WritePath:
        self.runs.append(run)
        return self._write_path

    def next_attempt(self, platform: Platform, stage: Stage) -> int:
        self.attempt_calls.append((platform, stage))
        return 7


class FakeExperimentRun:
    def __init__(self) -> None:
        self.params: dict[str, Any] = {}
        self.metrics: dict[str, Any] = {}
        self.ended_state: Any = None

    def log_params(self, params: dict[str, Any]) -> None:
        # SDK の実挙動（float / int / str 以外は TypeError）をここでも強制する
        for key, value in params.items():
            if not isinstance(key, str) or not isinstance(value, (float, int, str)):
                raise TypeError(f"{key}={value!r} は Experiments が受けない型")
        self.params.update(params)

    def log_metrics(self, metrics: dict[str, Any]) -> None:
        for key, value in metrics.items():
            if not isinstance(key, str) or not isinstance(value, (float, int, str)):
                raise TypeError(f"{key}={value!r} は Experiments が受けない型")
        self.metrics.update(metrics)

    def end_run(self, state: Any = None) -> None:
        self.ended_state = state


class FakeSdk:
    def __init__(self, *, explode: bool = False) -> None:
        self.created: list[tuple[str, str]] = []
        self.last_run: FakeExperimentRun | None = None
        self._explode = explode

        outer = self

        class ExperimentRun:
            @staticmethod
            def create(run_name: str, *, experiment: str) -> FakeExperimentRun:
                if outer._explode:
                    raise RuntimeError("Experiments 到達不能")
                outer.created.append((run_name, experiment))
                outer.last_run = FakeExperimentRun()
                return outer.last_run

        self.ExperimentRun = ExperimentRun


# --- 正本を動かさないこと ------------------------------------------------


def test_next_attempt_is_always_delegated_to_inner() -> None:
    """attempt の採番は Neon が正本。ここを移すと permission friction が別物になる。"""
    inner = SpyInner()
    sink = VertexExperimentSink(inner, experiment="exp", aiplatform=FakeSdk())

    assert sink.next_attempt(Platform.VERTEX, Stage.TRAIN) == 7
    assert inner.attempt_calls == [(Platform.VERTEX, Stage.TRAIN)]


def test_record_run_returns_inner_write_path() -> None:
    """write_path の判定は inner のもの。複写側が横取りすると到達経路が嘘になる。"""
    inner = SpyInner(write_path=WritePath.COLLECTED)
    sink = VertexExperimentSink(inner, experiment="exp", aiplatform=FakeSdk())

    assert sink.record_run(make_run()) is WritePath.COLLECTED
    assert len(inner.runs) == 1


def test_mirror_failure_does_not_reach_the_caller() -> None:
    """telemetry は非致命。複写が落ちても Neon の記録と戻り値は変えない。"""
    inner = SpyInner()
    sink = VertexExperimentSink(inner, experiment="exp", aiplatform=FakeSdk(explode=True))

    assert sink.record_run(make_run()) is WritePath.DIRECT
    assert len(inner.runs) == 1


def test_non_vertex_runs_are_not_mirrored() -> None:
    """Experiments は Vertex のサービス。他基盤を入れると5基盤が揃って見える誤解を生む。"""
    sdk = FakeSdk()
    sink = VertexExperimentSink(SpyInner(), experiment="exp", aiplatform=sdk)

    sink.record_run(make_run(platform=Platform.SNOWFLAKE))

    assert sdk.created == []


# --- Experiments 側の表現 ------------------------------------------------


def test_six_columns_are_mirrored_as_params() -> None:
    """比較6列が Experiments 側にも載ること（引けるかは別問題・docstring 参照）。"""
    sdk = FakeSdk()
    sink = VertexExperimentSink(SpyInner(), experiment="exp", aiplatform=sdk)

    sink.record_run(make_run(status=Status.FAILURE))

    assert sdk.last_run is not None
    params = sdk.last_run.params
    assert params["platform"] == "vertex"
    assert params["stage"] == "train"
    assert params["attempt"] == 3
    assert params["write_path"] == "direct"
    assert params["failure_class"] == "iam"


def test_status_maps_to_the_native_state() -> None:
    """status だけは native な列（Execution.State）に落ちる。"""
    from google.cloud.aiplatform_v1.types.execution import Execution

    sdk = FakeSdk()
    sink = VertexExperimentSink(SpyInner(), experiment="exp", aiplatform=sdk)

    sink.record_run(make_run(status=Status.FAILURE))
    assert sdk.last_run is not None
    assert sdk.last_run.ended_state is Execution.State.FAILED

    sink.record_run(make_run(status=Status.SUCCESS))
    assert sdk.last_run.ended_state is Execution.State.COMPLETE


@pytest.mark.parametrize(
    "value",
    [{"nested": 1}, ["a"], None, True],
    ids=["dict", "list", "none", "bool"],
)
def test_unsupported_param_types_are_stringified(value: Any) -> None:
    """SDK は float / int / str 以外を TypeError にする。

    素通しすると **複写だけが落ちる**（Neon は正しい）状態になり、
    「Experiments に無い run がある」理由が分からなくなる。
    bool を含めるのは `isinstance(True, int)` が真で、int として通ると
    Experiments 側に 1 / 0 で載って読めなくなるため。
    """
    params = to_params(make_run(params={"odd": value}))

    assert isinstance(params["odd"], str)


def test_metrics_include_duration_and_survive_type_check() -> None:
    metrics = to_metrics(make_run(metrics={"rmse": 0.43, "note": {"x": 1}}))

    assert metrics["duration_seconds"] == 12.5
    assert metrics["rmse"] == 0.43
    assert isinstance(metrics["note"], str)


def test_run_name_is_lowercase_and_bounded() -> None:
    """Experiments のリソース ID 制約（小文字英数とハイフン）に収める。"""
    name = experiment_run_name(make_run())

    assert name == "train-0d5f7d2e-1111-4222-8333-444444444444"
    assert name == name.lower()
    assert len(name) <= 128


# --- 配線（factory.build_sink）-------------------------------------------


def test_mirror_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """既定は無効。設定しない限りクラウドへの書き込みを増やさない。"""
    from platforms.shared import factory

    monkeypatch.delenv(factory.VERTEX_EXPERIMENT_ENV, raising=False)
    inner = SpyInner()

    assert factory.with_experiment_mirror(Platform.VERTEX, inner) is inner


def test_mirror_wraps_only_vertex(monkeypatch: pytest.MonkeyPatch) -> None:
    from platforms.shared import factory

    monkeypatch.setenv(factory.VERTEX_EXPERIMENT_ENV, "mcml-dev")
    inner = SpyInner()

    assert factory.with_experiment_mirror(Platform.SNOWFLAKE, inner) is inner
    assert isinstance(factory.with_experiment_mirror(Platform.VERTEX, inner), VertexExperimentSink)
