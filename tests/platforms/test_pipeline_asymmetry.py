"""Tier A 3基盤の「パイプライン化」の非対称を固定する。

**Vertex で見送ったから AWS / Azure でも見送る、は誤り。制約が違う。**

実測で確定した差（2026-08-02）:

    Vertex    : step = **コンテナ実行**（KFP）
                → run_phase.py を動かす器（オーケストレータイメージ）が要る。
                  学習イメージには scripts/ も adapter も aiplatform も無い
                  （依存最小の設計どおり）→ 実投入で exit 2。**P3 見送り**
    SageMaker : step = **学習ジョブの型付き宣言**（Training ステップの Arguments が
                  CreateTrainingJob のリクエストそのもの）→ 間に立つコンテナが無い。
                  **器の問題が発生しない**
    Azure ML  : step = **command job の合成**（dsl.pipeline）→ 同上

したがって守るのは:

  1. AWS / Azure のステップが **CLI 投入と同じ仕様**から作られること
     （別々に組むと「CLI では通るがパイプラインでは落ちる」差が生まれ比較が濁る）
  2. **新しいイメージを要求しないこと**（器が要るなら Vertex と同じ結論になる）
  3. Vertex のパイプライン実装が**復活していない**こと（P3 見送りのまま）
"""

from __future__ import annotations

import json
from typing import Any

from tests.fakes import sagemaker as sagemaker_fake
from tests.fakes.telemetry import InMemorySink

from platforms.sagemaker.pipeline import build_definition, definition_json


def training_request() -> dict[str, Any]:
    adapter, _, _ = sagemaker_fake.build(InMemorySink())
    return adapter.training_request("run-1", 2, {"n_estimators": 10}, {"NEON": "x"})


# --- SageMaker: 学習ジョブの宣言をそのまま載せる ---------------------------


def test_step_arguments_are_the_same_request_as_cli_submission() -> None:
    """ステップの Arguments が CLI 投入と同一であること。

    ここが別物になると「パイプラインでも同じことをした」が言えず、比較が濁る。
    """
    request = training_request()
    step = build_definition(request)["Steps"][0]

    assert step["Type"] == "Training"
    for key, value in request.items():
        if key == "TrainingJobName":
            continue
        assert step["Arguments"][key] == value


def test_training_job_name_is_dropped() -> None:
    """Pipelines は実行ごとにジョブ名を採番する。固定名を残すと2回目が名前衝突で落ちる。"""
    step = build_definition(training_request())["Steps"][0]

    assert "TrainingJobName" not in step["Arguments"]


def test_definition_carries_the_training_image_not_an_orchestrator_image() -> None:
    """**新しいイメージを要求しない**こと（器が要るなら Vertex と同じ結論になる）。

    ステップが動かすのは学習イメージそのもの。orchestrator 用の別イメージが
    現れたら、それは SageMaker でも P3 と同じ判断が要るというサイン。
    """
    request = training_request()
    step = build_definition(request)["Steps"][0]

    assert (
        step["Arguments"]["AlgorithmSpecification"]["TrainingImage"]
        == request["AlgorithmSpecification"]["TrainingImage"]
    )


def test_definition_is_serialisable_for_create_pipeline() -> None:
    """`CreatePipeline` の `PipelineDefinition` は JSON 文字列。"""
    parsed = json.loads(definition_json(training_request()))

    assert parsed["Version"] == "2020-12-01"
    assert [s["Name"] for s in parsed["Steps"]] == ["Train"]


def test_experiment_config_survives_into_the_pipeline_step() -> None:
    """実験の関連付けも投入時パラメータなので、ステップにそのまま載る。"""
    adapter, _, _ = sagemaker_fake.build(InMemorySink(), experiment="mcml-dev")
    request = adapter.training_request("run-1", 1, {}, {})

    step = build_definition(request)["Steps"][0]

    assert step["Arguments"]["ExperimentConfig"]["ExperimentName"] == "mcml-dev"


# --- 器を要求しないこと（3基盤の分かれ目）----------------------------------


def test_neither_aws_nor_azure_pipeline_needs_a_new_image() -> None:
    """パイプライン定義が `run_phase` / orchestrator を要求しないこと。

    Vertex はここで `scripts/run_phase.py` をステップの command に置いたため
    器が必要になり、実投入で落ちた。AWS / Azure は宣言的なので置く必要がない。
    """
    import ast
    import inspect

    from platforms.azureml import pipeline as azure_pipeline
    from platforms.sagemaker import pipeline as aws_pipeline

    for module in (aws_pipeline, azure_pipeline):
        # **docstring は対象外**（Vertex の反証を説明するために run_phase に言及する）。
        # 見るのはコードが実際に埋め込む文字列だけ。
        tree = ast.parse(inspect.getsource(module))
        # docstring ノード（Expr の直下の文字列定数）を id で除外する
        doc_nodes = set()
        for node in ast.walk(tree):
            for child in getattr(node, "body", []) or []:
                if isinstance(child, ast.Expr) and isinstance(child.value, ast.Constant):
                    doc_nodes.add(id(child.value))
        code_literals = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in doc_nodes
        ]

        offenders = [s for s in code_literals if "run_phase" in s or "orchestrator" in s]
        assert not offenders, f"{module.__name__} が orchestrator を要求している: {offenders}"


def test_vertex_pipeline_implementation_stays_removed() -> None:
    """Vertex のパイプライン実装は P3（見送り）で撤去済み。復活していないこと。

    復活させるなら器（オーケストレータイメージ）の追加とセットで、
    owner 判断が要る（修正11 のノート）。
    """
    from pathlib import Path

    from tests.conftest import REPO_ROOT

    assert not (REPO_ROOT / "src" / "platforms" / "vertex" / "pipeline.py").exists()
    assert not Path(REPO_ROOT / "scripts" / "compile_pipeline.py").exists()


# --- Azure: 同じ job を合成すること -----------------------------------------


def test_azure_pipeline_composes_the_adapter_job() -> None:
    """Azure のステップが adapter の `training_job` を呼ぶこと（組み直さない）。"""
    import inspect

    from platforms.azureml import pipeline as azure_pipeline

    source = inspect.getsource(azure_pipeline.build_pipeline)

    assert "adapter.training_job(" in source
    assert "entities.command(" not in source, "パイプライン側で job を組み直している"


def test_azure_adapter_exposes_the_job_builder() -> None:
    """CLI 投入経路も同じ `training_job` を通ること。"""
    import inspect

    from platforms.azureml.adapter import AzureMLAdapter

    assert callable(AzureMLAdapter.training_job)
    submit_source = inspect.getsource(AzureMLAdapter.submit_training)
    assert "self.training_job(" in submit_source
