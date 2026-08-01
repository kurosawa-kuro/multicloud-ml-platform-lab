"""SageMaker adapter の**固有**検証。共通契約は test_adapter_contract.py。

ここに置くのは SageMaker でしか成り立たない形:

  - `/opt/ml` 契約（ContainerEntrypoint / ChannelName / hyperparameters の畳み込み）
  - Managed Spot の上限（MaxWait >= MaxRuntime）
  - deploy が Model → EndpointConfig → Endpoint の**積み上げ**
  - teardown が依存の逆順、かつ Model Package を**残す**
"""

from __future__ import annotations

import json

from tests.fakes import sagemaker as fake
from tests.fakes.sagemaker import ARTIFACT_URI, FakeRuntime, FakeSageMaker

from core.telemetry.schemas import FailureClass, Status
from core.telemetry.sinks import JsonlRunSink
from platforms.sagemaker.adapter import (
    ENTRYPOINT_PATH,
    PARAMS_HYPERPARAMETER_KEY,
    TRAINING_CHANNEL,
)

CONFIG = fake.config()


def test_training_job_follows_the_opt_ml_contract(sink: JsonlRunSink) -> None:
    adapter, sm, _ = fake.build(sink)

    run = adapter.submit_training({"num_leaves": 31})

    assert run.status is Status.SUCCESS
    request = sm.training_requests[0]
    assert request["AlgorithmSpecification"]["ContainerEntrypoint"] == ["bash", ENTRYPOINT_PATH]
    assert request["AlgorithmSpecification"]["TrainingImage"] == CONFIG.training_image_uri
    assert request["InputDataConfig"][0]["ChannelName"] == TRAINING_CHANNEL
    assert request["RoleArn"] == CONFIG.execution_role_arn
    # 値は文字列限定なので JSON を1キーに畳む（Vertex には要らない手当て）
    folded = request["HyperParameters"][PARAMS_HYPERPARAMETER_KEY]
    assert isinstance(folded, str)
    assert json.loads(folded) == {"num_leaves": 31}
    assert run.params["model_artifact_uri"] == ARTIFACT_URI


def test_spot_is_enabled_with_a_wait_bound(sink: JsonlRunSink) -> None:
    adapter, sm, _ = fake.build(sink)

    adapter.submit_training({})

    condition = sm.training_requests[0]["StoppingCondition"]
    assert sm.training_requests[0]["EnableManagedSpotTraining"] is True
    assert condition["MaxWaitTimeInSeconds"] >= condition["MaxRuntimeInSeconds"]


def test_failed_job_status_is_classified_from_failure_reason(sink: JsonlRunSink) -> None:
    """DescribeTrainingJob の FailureReason から分類する（シムが書いた理由が効く）。"""
    adapter, _, _ = fake.build(sink, FakeSageMaker(job_status="Failed"))

    run = adapter.submit_training({})

    assert run.status is Status.FAILURE
    assert run.failure_class is FailureClass.IAM


def test_register_model_approves_the_package(sink: JsonlRunSink) -> None:
    """承認ステップがあるのは5基盤で SageMaker だけ。"""
    adapter, sm, _ = fake.build(sink)

    run = adapter.register_model(ARTIFACT_URI)

    package = sm.model_packages[0]
    assert package["ModelPackageGroupName"] == CONFIG.model_package_group_name
    assert package["ModelApprovalStatus"] == "Approved"
    assert package["InferenceSpecification"]["Containers"][0]["ModelDataUrl"] == ARTIFACT_URI
    assert package["InferenceSpecification"]["Containers"][0]["Image"] == CONFIG.serving_image_uri
    assert run.params["approval_required"] is True


def test_deploy_stacks_model_config_endpoint_in_order(sink: JsonlRunSink) -> None:
    adapter, sm, _ = fake.build(sink)

    run = adapter.deploy(ARTIFACT_URI)

    assert sm.calls == ["create_model", "create_endpoint_config", "create_endpoint"]
    assert run.params["reused_endpoint"] is False
    variant = sm.endpoint_configs[0]["ProductionVariants"][0]
    assert variant["InstanceType"] == CONFIG.endpoint_instance_type
    assert variant["ModelName"] == sm.models[0]["ModelName"]


def test_deploy_updates_existing_endpoint(sink: JsonlRunSink) -> None:
    adapter, sm, _ = fake.build(sink, FakeSageMaker(endpoint_exists=True))

    run = adapter.deploy(ARTIFACT_URI)

    assert "update_endpoint" in sm.calls
    assert "create_endpoint" not in sm.calls
    assert run.params["reused_endpoint"] is True


def test_predict_one_uses_the_shared_request_shape(sink: JsonlRunSink) -> None:
    adapter, _, runtime = fake.build(sink)

    run = adapter.predict_one({"MedInc": 8.3})

    assert run.metrics["prediction"] == 4.2
    body = json.loads(runtime.requests[0]["Body"])
    # 3契約共通の形（core/app/api/routes.py の PredictRequest）
    assert body == {"instances": [{"MedInc": 8.3}]}
    assert runtime.requests[0]["ContentType"] == "application/json"


def test_predict_one_with_empty_predictions_fails(sink: JsonlRunSink) -> None:
    adapter, _, _ = fake.build(sink, runtime=FakeRuntime({"predictions": []}))

    run = adapter.predict_one({"MedInc": 8.3})

    assert run.status is Status.FAILURE


def test_teardown_deletes_in_reverse_dependency_order(sink: JsonlRunSink) -> None:
    """逆順で消さないと「使用中」で失敗する。"""
    adapter, sm, _ = fake.build(sink)
    adapter.deploy(ARTIFACT_URI)
    sm.calls.clear()

    adapter.teardown()

    assert sm.calls == ["delete_endpoint", "delete_endpoint_config", "delete_model"]
    assert adapter.model_name is None
    assert adapter.endpoint_config_name is None


def test_teardown_keeps_model_package_as_residual(sink: JsonlRunSink) -> None:
    """Model Package は消さない = SageMaker 側の残留として比較表に載る。"""
    adapter, sm, _ = fake.build(sink)
    adapter.register_model(ARTIFACT_URI)

    run = adapter.teardown()

    assert "delete_model_package" not in sm.calls
    assert run.params["residual_model_package"] == adapter.model_package_arn
