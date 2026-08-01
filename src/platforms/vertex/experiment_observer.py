"""Vertex AI Experiments へ run を複写する observer（A: 併存 の実装）。

## 位置づけ —— Vertex adapter の内部部品（共通層には存在しない）

**`ml_runs` の正本には一切触らない。** 呼び出し元は `VertexAdapter._tracked` の
override だけで、共通層（`TrackedOperations` / factory / core）はこのモジュールを知らない。

ここに至る2段の作り直し（2026-08-02）:

  1. **sink の decorator として実装** → 学習成功行が複写されない（成功行はジョブ側が
     書く規約で、decorator は抑制の下流）＋ `merge_run_params` を落として再開を壊した。
  2. **core に RunObserver Protocol を新設して共通層へフック** → 動くが、実装が1つしか
     無い抽象を core に置いた。`ports.py` の「**5基盤ぶんの実装が並ぶものだけ port に
     する**。抽象化が目的化すると比較したい差が隠れる」への違反。
     5つのインフラから共通層を導くなら、単一基盤の関心は共通層に置けない。

よって最終形は **Vertex adapter 内の override**。学習成功行も複写され（super() の戻り値は
抑制と無関係に手元にある）、共通層は無傷で、有効化は `VertexConfig.experiment`
（env なら `MCML_VERTEX_EXPERIMENT` —— config 解決規約 `MCML_<PLATFORM>_<FIELD>` そのもの）。

## なぜ「置換」ではなく「併存」か

判定は `docs/tasks/04_verifying/2026-08-02-修正10-…`。要点だけ:

- 本ラボの比較は **5基盤を1テーブルで並べる**ことが前提（UC-003）。Experiments は
  Vertex の中で並べるサービスなので、置換すると横断集計層を自前で作る羽目になる。
- `attempt` は Neon の同一 (platform, stage) 過去行数 + 1 が正本。動かすと
  permission friction の計測が根元から変わる。

## 6列の置き場（SDK 1.163.0 の実 API で確認）

`log_params` / `log_metrics` は `Dict[str, float | int | str]` しか受けず、型は実行時に
検査される。比較6列のうち **native なスロットがあるのは `status` だけ**で、残りは params。

| `ml_runs` の列 | Experiments 側 | 比較軸として引けるか |
|---|---|---|
| `status` | `ExperimentRun` の state | ○ native |
| `platform` / `stage` / `attempt` / `failure_class` / `write_path` | params | △ 文字列・数値 |

**「格納できる」と「5基盤比較の軸として引ける」は別。** 併存を選ぶ理由そのもの。

## 観測の限界（依存最小の制約から来る境界）

学習成功行の `metrics`（RMSE 等）は**ジョブが書いた Neon の行にしか無い**。
学習コンテナは Vertex SDK を持てない（`docker/training/Dockerfile` の依存最小制約）ので、
ジョブ側から複写することもできない。よって Experiments 上の学習 run は
`attempt` / `status` / `duration_seconds` / `params` までで、**モデル品質は含まれない**。

## 非致命

観測は計測ではない。失敗しても Neon の記録と adapter の戻り値は変えない
（`docs/06_error_policy.md`）。
"""

from __future__ import annotations

import logging
from typing import Any

from core.telemetry.schemas import MlRun, Platform, Status

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


class VertexExperimentObserver:
    """`RunObserver` の実装。Vertex の run を Experiments へ複写する。

    **sink ではない。** `record_run` も `next_attempt` も `merge_run_params` も持たない
    ＝ 記録の契約を一切背負わないので、落として壊す経路が構造的に存在しない。

    Vertex 以外の run は複写しない（Experiments は Vertex のサービスであり、
    他基盤の run を混ぜると「Experiments に5基盤が揃っている」という誤解を生む）。
    """

    def __init__(self, *, experiment: str, aiplatform: Any | None = None) -> None:
        self._experiment = experiment
        self._aiplatform = aiplatform

    @property
    def sdk(self) -> Any:
        """`google.cloud.aiplatform` を遅延 import する（他基盤の実行を重くしない）。"""
        if self._aiplatform is None:
            from google.cloud import aiplatform  # noqa: PLC0415 - 遅延 import は意図的

            self._aiplatform = aiplatform
        return self._aiplatform

    def observe(self, run: MlRun) -> None:
        """`RunObserver` の契約。Vertex の run だけ複写する。"""
        if run.platform is not Platform.VERTEX:
            return
        self._mirror(run)

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
