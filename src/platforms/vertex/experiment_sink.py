"""Vertex AI Experiments へ run を複写する sink（A: 併存 の実装）。

## なぜ「置換」ではなく「併存」か

判定は `docs/tasks/02_backlog/2026-08-02-修正10-マネージド実験管理載せ替え試行.md`。
要点だけ再掲する（詳細を二重に持たない）:

- 本ラボの比較は **5基盤を1つのテーブルで並べる**ことが前提（要件 UC-003）。
  Experiments は Vertex の中で実験を並べるサービスなので、置換すると
  SageMaker / Azure ML / Databricks / Snowflake を横断集計する層を**自前で**作る羽目になる。
  「自前実装を減らす」動機と正面から矛盾する。
- `attempt` は Neon の同一 (platform, stage) 過去行数 + 1 で決まる
  （`platforms.shared.contracts.tracking.TrackedOperations._next_attempt`）。
  この採番の正本を動かすと permission friction の計測が根元から変わる。

したがって **Neon が正本のまま**で、Vertex の run だけを Experiments にも残す。
`ml_runs` のスキーマ・`sql/comparison_queries.sql`・`PlatformAdapter` の契約は不変。

## 6列の置き場（2026-08-02・SDK 1.163.0 の実 API で確認）

`log_params` / `log_metrics` は `Dict[str, float | int | str]` しか受けず、
値の型は実行時に検査される（`TypeError`）。つまり比較6列のうち Experiments に
**一級のスロットがあるのは status 相当だけ**で、残りは params の文字列になる。

| ml_runs の列 | Experiments 側 | 比較軸として引けるか |
|---|---|---|
| `status` | `ExperimentRun` の state（COMPLETE / FAILED） | ○（native） |
| `platform` | params（常に vertex） | △ 単一基盤なので軸にならない |
| `stage` | params | △ 文字列 |
| `attempt` | params（int） | △ 採番の正本は Neon 側 |
| `failure_class` | params | △ 文字列 |
| `write_path` | params | △ 文字列 |

**「格納できるか」は Yes、「5基盤比較の軸として引けるか」は No。** この非対称が
併存を選ぶ理由そのものなので、ここに記録して runbook 側と二重管理しない。

## 非致命

telemetry は非致命（docs/06_error_policy.md）。Experiments への複写に失敗しても
Neon への記録と adapter の戻り値は変えない。**複写は観測であって計測ではない。**
"""

from __future__ import annotations

import logging
from typing import Any

from core.telemetry.schemas import MlRun, Platform, Stage, Status, WritePath
from core.telemetry.tracking import RunSink

_logger = logging.getLogger("platforms.vertex.experiments")

# Experiments のリソース ID は小文字英数とハイフンのみ。run_id は uuid4 なのでそのまま
# 使えるが、stage を足して1 run 1エントリにする（同じ run_id で stage が違う行が並ぶため）。
_RUN_NAME_MAX = 128


def experiment_run_name(run: MlRun) -> str:
    """Experiments 側の run 名。`<stage>-<run_id>` を小文字で。

    `ml_runs` は (run_id, stage) の組で1行になりうる（train と register が同じ
    run_id を共有することはないが、将来そうなっても衝突しない形にしておく）。
    """
    return f"{run.stage.value}-{run.run_id}".lower()[:_RUN_NAME_MAX]


def to_params(run: MlRun) -> dict[str, float | int | str]:
    """比較6列と adapter が足した params を、Experiments が受ける型へ落とす。

    `log_params` は float / int / str しか受けない（SDK が実行時に TypeError）。
    dict や None を素通しすると複写だけが落ちるので、ここで潰す。
    """
    params: dict[str, float | int | str] = {
        "platform": run.platform.value,
        "stage": run.stage.value,
        "attempt": run.attempt,
        "write_path": run.write_path.value,
        "code_revision": run.code_revision,
    }
    if run.failure_class is not None:
        params["failure_class"] = run.failure_class.value
    for key, value in run.params.items():
        if isinstance(value, bool):  # bool は int のサブクラスなので先に文字列化する
            params[key] = str(value)
        elif isinstance(value, (int, float, str)):
            params[key] = value
        else:
            params[key] = str(value)
    return params


def to_metrics(run: MlRun) -> dict[str, float | int | str]:
    """metrics も同じ型制約に合わせる。duration は常に載せる（stage 別所要の実感用）。"""
    metrics: dict[str, float | int | str] = {}
    if run.duration_seconds is not None:
        metrics["duration_seconds"] = run.duration_seconds
    for key, value in run.metrics.items():
        if isinstance(value, bool):
            metrics[key] = str(value)
        elif isinstance(value, (int, float, str)):
            metrics[key] = value
        else:
            metrics[key] = str(value)
    return metrics


class VertexExperimentSink:
    """`RunSink` を満たしつつ、Vertex の run を Experiments にも複写する。

    **decorator であって置換ではない。** `record_run` / `next_attempt` とも
    `inner`（Neon か JSONL）へ委譲し、戻り値も inner のものをそのまま返す。
    Experiments への複写は副作用として足すだけなので、
    `write_path` の判定も attempt の採番も inner が持ったまま動く。

    Vertex 以外の run は複写しない（Experiments は Vertex のサービスであり、
    他基盤の run を混ぜると「Experiments に5基盤が揃っている」という誤解を生む）。
    """

    def __init__(
        self,
        inner: RunSink,
        *,
        experiment: str,
        aiplatform: Any | None = None,
    ) -> None:
        self._inner = inner
        self._experiment = experiment
        self._aiplatform = aiplatform

    @property
    def sdk(self) -> Any:
        """`google.cloud.aiplatform` を遅延 import する（他基盤の実行を重くしない）。"""
        if self._aiplatform is None:
            from google.cloud import aiplatform  # noqa: PLC0415 - 遅延 import は意図的

            self._aiplatform = aiplatform
        return self._aiplatform

    def record_run(self, run: MlRun) -> WritePath:
        write_path = self._inner.record_run(run)
        if run.platform is Platform.VERTEX:
            self._mirror(run)
        return write_path

    def next_attempt(self, platform: Platform, stage: Stage) -> int:
        """**必ず inner に委譲する。** 採番の正本を Neon から動かさない。"""
        return self._inner.next_attempt(platform, stage)

    def _mirror(self, run: MlRun) -> None:
        """Experiments へ1 run 複写する。失敗しても呼び出し元へ伝えない。

        **実験そのものを先に用意する**。`ExperimentRun.create` は内部で
        `_get_experiment()` を呼ぶだけで作らないため、未作成のまま渡すと落ちる
        （SDK 1.163.0 の実装を読んで確認）。`get_or_create` は冪等なので
        毎 run 呼んでよい。
        """
        try:
            self.sdk.Experiment.get_or_create(self._experiment)
            experiment_run = self.sdk.ExperimentRun.create(
                experiment_run_name(run), experiment=self._experiment
            )
            experiment_run.log_params(to_params(run))
            metrics = to_metrics(run)
            if metrics:
                experiment_run.log_metrics(metrics)
            experiment_run.end_run(state=self._state_for(run))
        except Exception:  # noqa: BLE001 - telemetry は非致命（06_error_policy）
            _logger.warning("Experiments への複写に失敗（Neon の記録は済んでいる）", exc_info=True)

    def _state_for(self, run: MlRun) -> Any:
        """`status` を Experiments の Execution.State へ写す（唯一 native な列）。"""
        from google.cloud.aiplatform_v1.types.execution import Execution  # noqa: PLC0415

        return Execution.State.COMPLETE if run.status is Status.SUCCESS else Execution.State.FAILED
