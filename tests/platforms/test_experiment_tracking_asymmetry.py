"""5基盤の「実験追跡」の非対称を固定する。

**この機能は5基盤で形が違う。揃えてはならない。**
揃えようとすると、どこかの基盤で嘘をつく（無い口を作る / 常時 ON を無効化できると偽る）。
02_architecture「設計の導出順序」手順3の「吸収せず記録する」の実装。

実測で確定した制約（2026-08-02・installed SDK の service model / signature）:

    Vertex     : ExperimentRun.create（別サービス）= 事後 API。全 stage が載る
    SageMaker  : CreateTrainingJob.ExperimentConfig = 投入時パラメータ。
                 **学習だけ**（CreateModel / CreateEndpoint / CreateModelPackage に口が無い）
    Azure ML   : command(experiment_name=...) = 投入時パラメータ。job は常に実験に属す
    Databricks : MLflow experiment のワークスペースパス = 基盤内蔵の別存在
    Snowflake  : **無い**

したがって守るのは「同じ実装であること」ではなく:

  1. 5基盤とも **同じ env 規約**（`MCML_<PLATFORM>_EXPERIMENT`）で扱えること
     —— 持たない Snowflake を除く
  2. 無い基盤（Snowflake）に**空フィールドを生やさない**こと
  3. 各基盤の形が実測どおりであること（事後 API / 投入パラメータ / 内蔵）
"""

from __future__ import annotations

import pytest

from core.telemetry.schemas import Platform
from platforms.azureml.adapter import AzureMlConfig
from platforms.databricks.adapter import DatabricksConfig
from platforms.sagemaker.adapter import SageMakerConfig
from platforms.snowflake.adapter import SnowflakeConfig
from platforms.vertex.adapter import VertexConfig

# 実験フィールドを持つ基盤と、その既定値の性質
HAS_EXPERIMENT = {
    Platform.VERTEX: VertexConfig,
    Platform.SAGEMAKER: SageMakerConfig,
    Platform.AZUREML: AzureMlConfig,
    Platform.DATABRICKS: DatabricksConfig,
}


@pytest.mark.parametrize("platform", sorted(HAS_EXPERIMENT, key=lambda p: p.value))
def test_experiment_field_is_named_uniformly(platform: Platform) -> None:
    """フィールド名が揃っていること = 同じ env 規約で上書きできること。

    `MCML_<PLATFORM>_<FIELD>` は field 名から決まるので、名前が揃わないと
    基盤ごとに別の env を覚える羽目になる（`experiment_name` と `experiment` が
    混在していた 2026-08-02 以前の状態）。
    """
    fields = HAS_EXPERIMENT[platform].__dataclass_fields__

    assert "experiment" in fields, f"{platform.value} の実験フィールド名が揃っていない"
    assert "experiment_name" not in fields


def test_snowflake_has_no_experiment_field() -> None:
    """**無いものは無いまま。** 空フィールドを置くと「あるが未設定」に見える。

    Snowflake の内蔵実験追跡は本ラボの範囲では使える口が無い。
    フィールドだけ生やすと、比較レポートで「設定すれば使える」と誤読される。
    """
    assert "experiment" not in SnowflakeConfig.__dataclass_fields__


@pytest.mark.parametrize(
    ("config_class", "default_is_none"),
    [
        (VertexConfig, True),
        (SageMakerConfig, True),
        (AzureMlConfig, False),
        (DatabricksConfig, False),
    ],
    ids=["vertex", "sagemaker", "azureml", "databricks"],
)
def test_default_reflects_whether_the_platform_can_opt_out(
    config_class: type, default_is_none: bool
) -> None:
    """既定値の差は基盤の制約そのもの。揃えてはいけない。

    Vertex / SageMaker は「実験に載せない」が選べる（既定 None = OFF）。
    Azure ML は job が**常に**実験に属し、Databricks は MLflow が内蔵なので
    「無効」という状態が無い（既定は実名）。ここを揃えると嘘になる。
    """
    default = config_class.__dataclass_fields__["experiment"].default

    assert (default is None) is default_is_none


def test_sagemaker_puts_experiment_on_the_training_request_not_after() -> None:
    """SageMaker は**投入時パラメータ**。事後 API を生やしていないこと。

    Vertex の形（`_tracked` override での事後複写）をコピーすると、
    `CreateTrainingJob` の `ExperimentConfig` を使わない別経路ができ、
    「SageMaker Experiments に載せた」が嘘になる。
    """
    import inspect

    from platforms.sagemaker import adapter as sagemaker_adapter

    source = inspect.getsource(sagemaker_adapter)

    assert "ExperimentConfig" in source, "投入リクエストに実験を載せていない"
    assert "_tracked(self" not in source, "SageMaker に事後観測の override が生えている"


def test_only_vertex_carries_a_post_hoc_observer() -> None:
    """事後 API を持つのは Vertex だけ（他基盤に observer を移植しない）。"""
    import inspect

    from platforms.azureml import adapter as azureml_adapter
    from platforms.databricks import adapter as databricks_adapter
    from platforms.snowflake import adapter as snowflake_adapter

    for module in (azureml_adapter, databricks_adapter, snowflake_adapter):
        source = inspect.getsource(module)
        assert "experiment_observer" not in source, f"{module.__name__} に observer が移植された"


def test_sagemaker_experiment_config_is_omitted_when_unset() -> None:
    """既定（None）では ExperimentConfig を送らない。空を送ると API が弾く。"""
    from tests.fakes import sagemaker as fake
    from tests.fakes.telemetry import InMemorySink

    adapter, sm, _ = fake.build(InMemorySink())
    adapter.submit_training({})

    assert "ExperimentConfig" not in sm.training_requests[0]


def test_sagemaker_experiment_config_is_sent_when_set() -> None:
    """設定時は投入リクエストに載り、学習の試行が実験に紐づくこと。"""
    from tests.fakes import sagemaker as fake
    from tests.fakes.telemetry import InMemorySink

    adapter, sm, _ = fake.build(InMemorySink(), experiment="mcml-dev")
    adapter.submit_training({})

    sent = sm.training_requests[0]["ExperimentConfig"]
    assert sent["ExperimentName"] == "mcml-dev"
    assert sent["TrialComponentDisplayName"].startswith("train-attempt-")
