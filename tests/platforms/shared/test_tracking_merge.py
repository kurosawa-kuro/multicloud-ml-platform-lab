"""学習成功行への params 追記（contracts/tracking._merge_job_row_params）。

学習の**成功行はジョブ側**が書く規約なので、adapter しか知らない値
（`model_artifact_uri`）はそのままでは Neon に残らない。実際 2026-08-01 の
5基盤の実測では train 成功行が全て `params={}` で、register / deploy を
単体で再開できず Azure では train からやり直した。

ここで固定するのは:

  1. 成功時に adapter 側 params が sink へ渡ること（＝ stage を跨げること）
  2. **失敗時には渡さないこと**（失敗行は adapter 自身が書いており二重追記は不要）
  3. merge が無い sink（JSONL fallback）でも落ちないこと
  4. 追記に失敗しても adapter の結果を変えないこと（telemetry 非致命）
"""

from __future__ import annotations

from typing import Any

import pytest

from core.telemetry.schemas import MlRun, Platform, Stage, Status, WritePath
from core.telemetry.tracking import RunContext
from platforms.shared.contracts.tracking import TrackedOperations


class MergingSink:
    """merge_run_params を持つ sink（Neon 相当）。"""

    def __init__(self, *, rows_updated: int = 1, explode: bool = False) -> None:
        self.merged: list[tuple[str, dict[str, Any]]] = []
        self.recorded: list[MlRun] = []
        self._rows_updated = rows_updated
        self._explode = explode

    def record_run(self, run: MlRun) -> WritePath:
        self.recorded.append(run)
        return WritePath.DIRECT

    def next_attempt(self, platform: Platform, stage: Stage) -> int:
        return 1

    def merge_run_params(self, run_id: str, params: dict[str, Any]) -> int:
        if self._explode:
            raise RuntimeError("Neon 到達不能")
        self.merged.append((run_id, params))
        return self._rows_updated


class PlainSink(MergingSink):
    """merge_run_params を持たない sink（JSONL fallback 相当）。"""

    merge_run_params = None  # type: ignore[assignment]


class Adapter(TrackedOperations):
    platform = Platform.AZUREML

    def __init__(self, sink: Any) -> None:
        self._sink = sink

    def train(self, *, fail: bool = False) -> MlRun:
        def call(ctx: RunContext) -> None:
            ctx.params["model_artifact_uri"] = "azureml://jobs/j/outputs/model"
            if fail:
                raise RuntimeError("投入失敗")

        return self._tracked(Stage.TRAIN, {}, call, job_owns_success=True)


def test_success_pushes_adapter_params_onto_the_job_row() -> None:
    sink = MergingSink()

    run = Adapter(sink).train()

    assert run.status is Status.SUCCESS
    assert sink.merged == [(run.run_id, {"model_artifact_uri": "azureml://jobs/j/outputs/model"})]


def test_success_row_itself_is_still_owned_by_the_job() -> None:
    """追記は行を作らない。成功行の INSERT は依然としてジョブ側の責任。"""
    sink = MergingSink()

    Adapter(sink).train()

    assert sink.recorded == [], "adapter が成功行を書いている（write_path を騙る）"


def test_failure_does_not_merge() -> None:
    """投入失敗時の行は adapter 自身が書くので、追記する相手が居ない。"""
    sink = MergingSink()

    run = Adapter(sink).train(fail=True)

    assert run.status is Status.FAILURE
    assert sink.merged == []


def test_row_absent_is_not_an_error() -> None:
    """collected 経路では行が `make collect` の後に現れる。0 件は異常ではない。"""
    sink = MergingSink(rows_updated=0)

    run = Adapter(sink).train()

    assert run.status is Status.SUCCESS


def test_sink_without_merge_support_is_tolerated() -> None:
    """JSONL fallback には merge が無い。属性の有無で落とさない。"""
    run = Adapter(PlainSink()).train()

    assert run.status is Status.SUCCESS


def test_merge_failure_does_not_change_the_run(caplog: pytest.LogCaptureFixture) -> None:
    """telemetry は非致命（docs/06_error_policy.md）。"""
    run = Adapter(MergingSink(explode=True)).train()

    assert run.status is Status.SUCCESS
