"""Azure ML Pipelines の定義（修正11 の Azure 版）。

## Vertex との決定的な差 —— 器（オーケストレータイメージ）が要らない

Vertex AI Pipelines のステップは **コンテナを動かす**。だから `run_phase.py` を
実行できる器が別途必要になり、学習イメージ（依存最小）では動かないことが実投入で
判明した（修正11 → P3 見送り）。

Azure ML Pipelines のステップは **command job そのもの**。`dsl.pipeline` は
既存の job を合成するだけで、間に立つコンテナが存在しない。**器の問題が発生しない。**

    Vertex    : step = コンテナ実行       → run_phase.py を動かす器が要る（無い）
    Azure ML  : step = command job の合成 → adapter が組む job をそのまま並べる

## 同じ仕様を2経路で使う

ステップに載せるのは `AzureMLAdapter.training_job()` の戻り値 ——
**CLI 投入とまったく同じ関数**。別々に組むと「CLI では通るがパイプラインでは落ちる」
差が生まれ、比較が濁る。

## 記録（ml_runs）はどうなるか

学習イメージは変わらないので entrypoint シムと `job_record` はそのまま動く
＝ `write_path` / `failure_class` / `attempt` の意味論は CLI 経路と同一。

## 非対象

register / deploy は入れない（**常時課金**。この定義で見たいのは
「学習が同じ意味論のままパイプラインから起こせるか」だけ）。
"""

from __future__ import annotations

from typing import Any


def build_pipeline(adapter: Any, run_id: str, attempt: int, params_json: str, job_env: dict) -> Any:
    """train 1ステップのパイプラインを組む（**投入はしない**）。

    `dsl.pipeline` はデコレータなので、ここで関数を定義して即座に呼び、
    `PipelineJob` エンティティを返す。実験名は job 側が既に持っている
    （Azure は job が常に実験に属する。`AzureMlConfig.experiment`）。
    """
    from azure.ai.ml import dsl  # noqa: PLC0415 - azure extra（既定の実行経路に載せない）

    @dsl.pipeline(
        name="mcml-train",
        display_name="mcml-train",
        experiment_name=adapter._config.experiment,
    )
    def _pipeline() -> None:
        # ステップ = CLI 投入とまったく同じ job。ここで組み直さないことが要点
        adapter.training_job(run_id, attempt, params_json, job_env)

    return _pipeline()
