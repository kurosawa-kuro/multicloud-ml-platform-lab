"""SageMaker の偽 SDK（boto3 sagemaker / sagemaker-runtime の代役）。"""

from __future__ import annotations

import io
import json
from typing import Any

from tests.fakes import AdapterCase, ExplodingClient

from core.telemetry.schemas import Platform
from core.telemetry.tracking import RunSink
from platforms.sagemaker.adapter import SageMakerAdapter, SageMakerConfig

ARTIFACT_URI = "s3://bucket/runs/job/output/model.tar.gz"


def config(**overrides: Any) -> SageMakerConfig:
    values: dict[str, Any] = {
        "region": "ap-northeast-1",
        "bucket": "mcml-dev-123456789012",
        "execution_role_arn": "arn:aws:iam::123456789012:role/mcml-dev-sagemaker-exec",
        "training_image_uri": "123.dkr.ecr.ap-northeast-1.amazonaws.com/mcml-dev-training:abc",
        "serving_image_uri": "123.dkr.ecr.ap-northeast-1.amazonaws.com/mcml-dev-serving:abc",
    }
    values.update(overrides)
    return SageMakerConfig(**values)


class FakeSageMaker:
    """boto3 sagemaker クライアントの最小代役。呼び出し順を記録する。"""

    def __init__(self, *, endpoint_exists: bool = False, job_status: str = "Completed") -> None:
        self.endpoint_exists = endpoint_exists
        self.job_status = job_status
        self.calls: list[str] = []
        self.training_requests: list[dict[str, Any]] = []
        self.model_packages: list[dict[str, Any]] = []
        self.models: list[dict[str, Any]] = []
        self.endpoint_configs: list[dict[str, Any]] = []
        self.endpoints: list[dict[str, Any]] = []

    def create_training_job(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("create_training_job")
        self.training_requests.append(kwargs)
        return {"TrainingJobArn": "arn:aws:sagemaker:...:training-job/x"}

    def describe_training_job(self, TrainingJobName: str) -> dict[str, Any]:  # noqa: N803
        self.calls.append("describe_training_job")
        return {
            "TrainingJobStatus": self.job_status,
            "FailureReason": "AccessDenied: not authorized to perform sagemaker:CreateTrainingJob",
            "ModelArtifacts": {"S3ModelArtifacts": ARTIFACT_URI},
        }

    def create_model_package(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("create_model_package")
        self.model_packages.append(kwargs)
        return {"ModelPackageArn": "arn:aws:sagemaker:...:model-package/mcml/1"}

    def create_model(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("create_model")
        self.models.append(kwargs)
        return {}

    def create_endpoint_config(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("create_endpoint_config")
        self.endpoint_configs.append(kwargs)
        return {}

    def create_endpoint(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("create_endpoint")
        self.endpoints.append(kwargs)
        self.endpoint_exists = True
        return {}

    def update_endpoint(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("update_endpoint")
        self.endpoints.append(kwargs)
        return {}

    def describe_endpoint(self, EndpointName: str) -> dict[str, Any]:  # noqa: N803
        if not self.endpoint_exists:
            raise RuntimeError(f"ValidationException: endpoint {EndpointName} not found")
        return {"EndpointStatus": "InService"}

    def delete_endpoint(self, EndpointName: str) -> dict[str, Any]:  # noqa: N803
        self.calls.append("delete_endpoint")
        self.endpoint_exists = False
        return {}

    def delete_endpoint_config(self, EndpointConfigName: str) -> dict[str, Any]:  # noqa: N803
        self.calls.append("delete_endpoint_config")
        return {}

    def delete_model(self, ModelName: str) -> dict[str, Any]:  # noqa: N803
        self.calls.append("delete_model")
        return {}


class FakeRuntime:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload if payload is not None else {"predictions": [4.2]}
        self.requests: list[dict[str, Any]] = []

    def invoke_endpoint(self, **kwargs: Any) -> dict[str, Any]:
        self.requests.append(kwargs)
        return {"Body": io.BytesIO(json.dumps(self.payload).encode())}


def build(
    sink: RunSink,
    sm: FakeSageMaker | None = None,
    runtime: FakeRuntime | None = None,
    **config_overrides: Any,
) -> tuple[SageMakerAdapter, FakeSageMaker, FakeRuntime]:
    sm = sm or FakeSageMaker()
    runtime = runtime or FakeRuntime()
    adapter = SageMakerAdapter(
        config(**config_overrides), sink=sink, sagemaker_client=sm, runtime_client=runtime
    )
    return adapter, sm, runtime


def case() -> AdapterCase:
    def make(sink: RunSink) -> SageMakerAdapter:
        adapter, _, _ = build(sink, FakeSageMaker(endpoint_exists=True))
        return adapter

    def make_failing(sink: RunSink) -> SageMakerAdapter:
        return SageMakerAdapter(
            config(),
            sink=sink,
            sagemaker_client=ExplodingClient("AccessDeniedException: not authorized"),
            runtime_client=ExplodingClient("AccessDeniedException: not authorized"),
        )

    return AdapterCase(
        platform=Platform.SAGEMAKER,
        make=make,
        make_failing=make_failing,
        model_ref=ARTIFACT_URI,
        artifact_uri=ARTIFACT_URI,
    )
