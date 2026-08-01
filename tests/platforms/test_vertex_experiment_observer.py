"""Vertex AI Experiments 複写（observer）の検証。

**Experiments を実際に叩かない**（SDK を注入する）。ここで守るのは
「観測を足しても計測の正本が動かない」ことと、
**学習成功行も観測される**こと（sink decorator だった頃の最大の欠陥）。

守る不変条件:
  - observer は sink の契約を一切持たない（記録経路から降りている）
  - `job_owns_success=True` の成功行 —— Neon へは書かれない行 —— も観測される
  - Vertex 以外の run は複写しない
  - 観測が失敗しても呼び出し元へ伝えない（telemetry は非致命）
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
from platforms.vertex.experiment_observer import (
    VertexExperimentObserver,
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
        self.ensured: list[str] = []
        self.last_run: FakeExperimentRun | None = None
        self._explode = explode

        outer = self

        class Experiment:
            @staticmethod
            def get_or_create(name: str) -> None:
                outer.ensured.append(name)

        self.Experiment = Experiment

        class ExperimentRun:
            @staticmethod
            def create(run_name: str, *, experiment: str) -> FakeExperimentRun:
                if outer._explode:
                    raise RuntimeError("Experiments 到達不能")
                outer.created.append((run_name, experiment))
                outer.last_run = FakeExperimentRun()
                return outer.last_run

        self.ExperimentRun = ExperimentRun


# --- 共通層に存在しないこと（outside-in の要点）----------------------------


def make_adapter(sdk: Any, sink: Any, experiment: str | None = "exp") -> Any:
    """実 VertexAdapter を fake SDK / sink で組む。配線そのものが検証対象。"""
    from platforms.vertex.adapter import VertexAdapter, VertexConfig

    config = VertexConfig(
        project="example-gcp-project",
        region="us-central1",
        bucket="example-bucket",
        training_image_uri="img",
        experiment=experiment,
    )
    return VertexAdapter(config, sink=sink, aiplatform=sdk)


def test_common_layers_do_not_know_the_observer() -> None:
    """共通層（core / TrackedOperations / factory）が Experiments を知らないこと。

    `ports.py` の「5基盤ぶんの実装が並ぶものだけ port にする」の番人。
    単一基盤の関心が共通層に漏れたら、ここで落ちる。
    """
    import inspect

    import platforms.shared.contracts.tracking as shared_tracking
    import platforms.shared.factory as factory
    from core.telemetry import tracking as core_tracking

    for module in (core_tracking, shared_tracking, factory):
        source = inspect.getsource(module)
        # docstring の「作り直した」注記は許す。コード（import / 呼び出し）を禁じる
        assert "experiment_observer" not in source, f"{module.__name__} が observer を import"
        assert "attach_observer" not in source, f"{module.__name__} に observer フック"


def test_observer_carries_no_sink_contract() -> None:
    """observer が sink の契約を持たないこと（決して記録経路に戻さない）。"""
    observer = VertexExperimentObserver(experiment="exp", aiplatform=FakeSdk())

    for name in ("record_run", "next_attempt", "merge_run_params"):
        assert not hasattr(observer, name), f"observer が sink の {name} を持っている"


def test_job_owned_success_is_still_observed() -> None:
    """**学習成功行も観測される。** sink decorator だった頃の最大の欠陥がここ。

    成功行はジョブ側が書く規約（`job_owns_success=True`）なので sink へは伝播しないが、
    `VertexAdapter._tracked` の override は super() の戻り値を抑制と無関係に受け取る。
    """
    from core.telemetry.schemas import Stage

    sdk = FakeSdk()
    inner = SpyInner()
    adapter = make_adapter(sdk, inner)

    adapter._tracked(Stage.TRAIN, {}, lambda ctx: None, job_owns_success=True)

    assert inner.runs == [], "成功行が Neon へ書かれている（行の所有規約が壊れた）"
    assert len(sdk.created) == 1, "学習成功行が観測されていない"


def test_observation_failure_does_not_reach_the_caller() -> None:
    """観測は非致命。落ちても adapter の結果を変えない。"""
    from core.telemetry.schemas import Stage

    adapter = make_adapter(FakeSdk(explode=True), SpyInner())

    run = adapter._tracked(Stage.REGISTER, {}, lambda ctx: None)

    assert run.status is Status.SUCCESS


def test_mirror_is_off_when_experiment_is_unset() -> None:
    """`VertexConfig.experiment` が None なら SDK に一切触れない（既定 OFF）。"""
    from core.telemetry.schemas import Stage

    sdk = FakeSdk()
    adapter = make_adapter(sdk, SpyInner(), experiment=None)

    adapter._tracked(Stage.REGISTER, {}, lambda ctx: None)

    assert sdk.created == [] and sdk.ensured == []


def test_experiment_field_resolves_via_standard_env_override() -> None:
    """`MCML_VERTEX_EXPERIMENT` が config 解決規約でフィールドに載ること。

    factory が独自に環境変数を読む形（旧設計）ではなく、
    `MCML_<PLATFORM>_<FIELD>` の既存規約そのものであることの確認。
    """
    from core.telemetry.schemas import Platform as P
    from platforms.shared.config import Settings

    settings = Settings(
        config={"platforms": {"vertex": {"region": "us-central1"}}},
        outputs={
            P.VERTEX: {
                "project_id": "example-gcp-project",
                "gcs_bucket": "example-bucket",
                "container_image_prefix": "example",
            }
        },
        environ={"MCML_VERTEX_EXPERIMENT": "mcml-dev"},
    )

    assert settings.for_platform(P.VERTEX).experiment == "mcml-dev"


def test_non_vertex_runs_are_not_mirrored() -> None:
    """Experiments は Vertex のサービス。他基盤を入れると5基盤が揃って見える誤解を生む。"""
    sdk = FakeSdk()
    observer = VertexExperimentObserver(experiment="exp", aiplatform=sdk)

    observer.observe(make_run(platform=Platform.SNOWFLAKE))

    assert sdk.created == []


# --- Experiments 側の表現 ------------------------------------------------


def test_six_columns_are_mirrored_as_params() -> None:
    """比較6列が Experiments 側にも載ること（引けるかは別問題・docstring 参照）。"""
    sdk = FakeSdk()
    observer = VertexExperimentObserver(experiment="exp", aiplatform=sdk)

    observer.observe(make_run(status=Status.FAILURE))

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
    observer = VertexExperimentObserver(experiment="exp", aiplatform=sdk)

    observer.observe(make_run(status=Status.FAILURE))
    assert sdk.last_run is not None
    assert sdk.last_run.ended_state is Execution.State.FAILED

    observer.observe(make_run(status=Status.SUCCESS))
    assert sdk.last_run.ended_state is Execution.State.COMPLETE


def test_experiment_is_created_before_the_run() -> None:
    """`ExperimentRun.create` は実験を作らない（`_get_experiment` を呼ぶだけ）。"""
    sdk = FakeSdk()
    observer = VertexExperimentObserver(experiment="mcml-dev", aiplatform=sdk)

    observer.observe(make_run())

    assert sdk.ensured == ["mcml-dev"]
    assert sdk.created == [("train-0d5f7d2e-1111-4222-8333-444444444444", "mcml-dev")]


@pytest.mark.parametrize(
    "value",
    [{"nested": 1}, ["a"], None, True],
    ids=["dict", "list", "none", "bool"],
)
def test_unsupported_param_types_are_stringified(value: Any) -> None:
    """SDK は float / int / str 以外を TypeError にする。

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


