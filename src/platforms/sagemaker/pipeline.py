"""SageMaker Pipelines の定義（修正11 の AWS 版）。

## Vertex との決定的な差 —— 器（オーケストレータイメージ）が要らない

Vertex AI Pipelines のステップは **コンテナを動かす**。だから
`run_phase.py` を実行できる器（オーケストレータイメージ）が別途必要になり、
学習イメージ（依存最小）では動かないことが実投入で判明した（修正11 → P3 見送り）。

SageMaker Pipelines のステップは **型付きの宣言**である。`Training` ステップの
`Arguments` は `CreateTrainingJob` のリクエストそのもので、SageMaker 側が
学習ジョブを起こす。**間に立つコンテナが存在しない**ので、器の問題が発生しない。

    Vertex     : step = コンテナ実行     → run_phase.py を動かす器が要る（無い）
    SageMaker  : step = 学習ジョブの宣言 → adapter が組む request をそのまま載せる

したがって「Vertex で見送ったから AWS でも見送る」は誤り。**制約が違う。**

## 同じ仕様を2経路で使う

ステップに載せるのは `SageMakerAdapter.training_request()` の戻り値 ——
**CLI 投入とまったく同じ関数**。別々に組むと「CLI では通るがパイプラインでは落ちる」
差が生まれ、比較が濁る。

## 記録（ml_runs）はどうなるか

学習コンテナは変わらないので、entrypoint シムと `job_record` はそのまま動く
＝ **`write_path` / `failure_class` / `attempt` の意味論は CLI 経路と同一**。
`attempt` は Neon の過去行数+1（プロセス外カウント）なので、誰が起こしても変わらない。

ただし `run_id` / `attempt` は**定義を作る時点で確定**する（パイプライン定義は静的）。
実行のたびに数え直したいなら、実行ごとに定義を作り直す。ここでは
「1定義 = 1回の比較試行」として扱う。

## 非対象

register / deploy は入れない。`CreateModel` / `CreateEndpoint` に相当する
ステップ型はあるが、**常時課金**が発生する上、この定義で見たいのは
「学習が同じ意味論のままパイプラインから起こせるか」だけ。
"""

from __future__ import annotations

import json
from typing import Any

# Pipelines の定義スキーマ版（AWS のドキュメント準拠）。
PIPELINE_DEFINITION_VERSION = "2020-12-01"


def build_definition(
    training_request: dict[str, Any],
    *,
    step_name: str = "Train",
    pipeline_name: str = "mcml-train",
) -> dict[str, Any]:
    """`CreateTrainingJob` のリクエストを Pipelines の定義へ包む。

    **リクエストは加工しない。** ここで値を書き換えると CLI 経路との同一性が崩れ、
    「パイプラインでも同じことをした」と言えなくなる。

    `TrainingJobName` は外す —— Pipelines は実行ごとにジョブ名を採番するため、
    固定名を渡すと2回目の実行が名前衝突で落ちる（定義は再利用される前提）。
    """
    arguments = {key: value for key, value in training_request.items() if key != "TrainingJobName"}
    return {
        "Version": PIPELINE_DEFINITION_VERSION,
        "Metadata": {},
        "Parameters": [],
        "Steps": [
            {
                "Name": step_name,
                "Type": "Training",
                "Arguments": arguments,
            }
        ],
    }


def definition_json(training_request: dict[str, Any], **kwargs: Any) -> str:
    """`CreatePipeline` の `PipelineDefinition` に渡す JSON 文字列。"""
    return json.dumps(build_definition(training_request, **kwargs))
