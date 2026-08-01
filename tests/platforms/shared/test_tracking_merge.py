"""stage 間の受け渡し（handoff）の永続化 —— `resume.persist_handoff`。

学習の**成功行はジョブ側**が書く規約なので、adapter しか知らない値
（`model_artifact_uri`）はそのままでは Neon に残らない。実際 2026-08-01 の
5基盤の実測では train 成功行が全て `params={}` で、register / deploy を
単体で再開できず Azure では train からやり直した。

## この検証が resume 側に居る理由（2026-08-02 再設計）

かつて追記は `TrackedOperations._merge_job_row_params` として**5基盤の共有基底**に、
契約は `RunSink.merge_run_params` として**全 sink** に居た。実装が意味を持つのは
Neon だけなのに契約で均した結果、JSONL に no-op・decorator に中継が生え、
**1つの修正が全 sink と共有基底へ波及する**構造になっていた。
受け渡しは driver（run_phase）の機能として `resume.py` に移した。

固定するのは:

  1. driver が train 成功の**直後に**永続化を呼ぶこと
  2. 失敗 run は永続化しないこと（失敗行は adapter 自身が書いており二重追記は不要）
  3. merge できない sink（JSONL = collected 系統）は **None で明示スキップ**
     —— silent no-op ではなく「できない」が戻り値で見えること
  4. 追記に失敗しても driver の結果を変えないこと（telemetry 非致命）
  5. 共有基底と RunSink 契約から merge が**消えたまま**であること
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from tests.conftest import load_script

from core.telemetry.schemas import MlRun, Platform, Stage, Status, Tier, UnificationUnit, WritePath
from platforms.shared.resume import SupportsHandoffMerge, persist_handoff


class MergingSink:
    """merge_run_params を持つ sink（Neon 相当）。"""

    def __init__(self, *, rows_updated: int = 1, explode: bool = False) -> None:
        self.merged: list[tuple[str, dict[str, Any]]] = []
        self._rows_updated = rows_updated
        self._explode = explode

    def record_run(self, run: MlRun) -> WritePath:
        return WritePath.DIRECT

    def next_attempt(self, platform: Platform, stage: Stage) -> int:
        return 1

    def merge_run_params(self, run_id: str, params: dict[str, Any]) -> int:
        if self._explode:
            raise RuntimeError("Neon 到達不能")
        self.merged.append((run_id, params))
        return self._rows_updated


class CollectedSink:
    """merge を持たない sink（JSONL fallback = collected 系統相当）。"""

    def record_run(self, run: MlRun) -> WritePath:
        return WritePath.COLLECTED

    def next_attempt(self, platform: Platform, stage: Stage) -> int:
        return 1


def make_run(
    *,
    status: Status = Status.SUCCESS,
    params: dict[str, Any] | None = None,
) -> MlRun:
    return MlRun(
        run_id="r-1",
        platform=Platform.AZUREML,
        tier=Tier.A,
        unification_unit=UnificationUnit.CONTAINER,
        stage=Stage.TRAIN,
        status=status,
        code_revision="a" * 40,
        write_path=WritePath.DIRECT,
        params=(
            params if params is not None else {"model_artifact_uri": "azureml://jobs/j/out/model"}
        ),
    )


# --- 永続化の本体 ----------------------------------------------------------


def test_handoff_is_persisted_via_a_merging_sink() -> None:
    sink = MergingSink()

    assert persist_handoff(make_run(), sink) == 1
    assert sink.merged == [("r-1", {"model_artifact_uri": "azureml://jobs/j/out/model"})]


def test_collected_sink_is_skipped_explicitly_not_silently() -> None:
    """None = 「この sink では merge できない」。0（相手がまだ居ない）と区別する。

    かつては契約上の no-op が 0 を返し、両者が見分けられなかった。
    系統差は隠さず戻り値で見せる。
    """
    assert persist_handoff(make_run(), CollectedSink()) is None


def test_row_absent_is_not_an_error() -> None:
    """direct 系統でもジョブ行の INSERT は僅かに遅れうる。0 件は異常ではない。"""
    assert persist_handoff(make_run(), MergingSink(rows_updated=0)) == 0


def test_empty_params_short_circuits() -> None:
    sink = MergingSink()

    assert persist_handoff(make_run(params={}), sink) == 0
    assert sink.merged == []


def test_merge_failure_does_not_reach_the_caller() -> None:
    """telemetry は非致命（docs/06_error_policy.md）。"""
    assert persist_handoff(make_run(), MergingSink(explode=True)) == 0


def test_neon_sink_satisfies_the_handoff_protocol() -> None:
    """実体（NeonRunSink）が書き側の契約を満たすことの pin。

    ここが外れると direct 系統でも handoff がスキップされ、resume が静かに死ぬ。
    """
    run_sink = pytest.importorskip("platforms.neon.run_sink")

    method = getattr(run_sink.NeonRunSink, "merge_run_params", None)
    assert callable(method)
    assert list(inspect.signature(method).parameters)[:3] == ["self", "run_id", "params"]


# --- driver（run_phase）からの配線 -----------------------------------------


class FakeAdapter:
    platform = Platform.AZUREML

    def __init__(self, run: MlRun) -> None:
        self._run = run

    def submit_training(self, params: dict[str, Any]) -> MlRun:
        return self._run


def test_run_steps_persists_handoff_right_after_train_success() -> None:
    """受け渡しは driver の責務。train 成功の直後に永続化されること。"""
    run_phase = load_script("run_phase")
    sink = MergingSink()

    run_phase.run_steps(
        FakeAdapter(make_run()), ("train",), params={}, instance=lambda: {}, sink=sink
    )

    assert sink.merged == [("r-1", {"model_artifact_uri": "azureml://jobs/j/out/model"})]


def test_run_steps_does_not_persist_failed_train() -> None:
    run_phase = load_script("run_phase")
    sink = MergingSink()

    run_phase.run_steps(
        FakeAdapter(make_run(status=Status.FAILURE)),
        ("train",),
        params={},
        instance=lambda: {},
        sink=sink,
    )

    assert sink.merged == []


# --- スパインの純化（merge が共通層から消えていること）----------------------


def test_spine_no_longer_carries_the_merge_concern() -> None:
    """共有基底と RunSink 契約に merge が存在しないこと。

    ここに戻したくなったら、それは「片実装の操作を契約で均す」誤りの再演
    （core/telemetry/tracking.py の RunSink docstring）。
    """
    import platforms.shared.contracts.tracking as shared
    from core.telemetry.tracking import RunSink

    assert not hasattr(RunSink, "merge_run_params")
    source = inspect.getsource(shared)
    assert "_merge_job_row_params" not in source
    assert "def merge_run_params" not in source


def test_handoff_protocol_is_localized_to_resume() -> None:
    """isinstance 判定の置き場は resume の1箇所だけ（散らばると duck-typing に戻る）。"""
    assert SupportsHandoffMerge.__module__ == "platforms.shared.resume"
