"""Vertex AI の偽 SDK（google.cloud.aiplatform の代役）。"""

from __future__ import annotations

from typing import Any

from tests.fakes import AdapterCase, ExplodingClient

from core.telemetry.schemas import Platform
from core.telemetry.tracking import RunSink
from platforms.vertex.adapter import VertexAdapter, VertexConfig

ARTIFACT_URI = "gs://example-gcp-project-mcml-dev/runs/r-1/model"
MODEL_REF = "projects/p/locations/r/models/1"


def config(**overrides: Any) -> VertexConfig:
    values: dict[str, Any] = {
        "project": "example-gcp-project",
        "region": "us-central1",
        "bucket": "example-gcp-project-mcml-dev",
        "training_image_uri": "us-central1-docker.pkg.dev/p/mcml/training:abc",
        "serving_image_uri": "us-central1-docker.pkg.dev/p/mcml/serving:abc",
        "service_account": "mcml-dev-vertex@example-gcp-project.iam.gserviceaccount.com",
    }
    values.update(overrides)
    return VertexConfig(**values)


class FakeJob:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.run_kwargs: dict[str, Any] | None = None
        self.resource_name = "projects/p/locations/r/customJobs/1"

    def run(self, **kwargs: Any) -> None:
        self.run_kwargs = kwargs


class FakeModel:
    def __init__(self, resource_name: str = MODEL_REF) -> None:
        self.resource_name = resource_name
        self.deploy_kwargs: dict[str, Any] | None = None

    def deploy(self, **kwargs: Any) -> None:
        self.deploy_kwargs = kwargs


class FakeEndpoint:
    def __init__(self, display_name: str = "mcml-dev-endpoint") -> None:
        self.display_name = display_name
        self.resource_name = "projects/p/locations/r/endpoints/1"
        self.calls: list[str] = []
        self.predictions: list[float] = [4.2]

    def undeploy_all(self) -> None:
        self.calls.append("undeploy_all")

    def delete(self, force: bool = False) -> None:
        self.calls.append(f"delete(force={force})")

    def predict(self, instances: list[dict[str, Any]]) -> Any:
        self.calls.append(f"predict({len(instances)})")
        predictions = self.predictions

        class Response:
            def __init__(self) -> None:
                self.predictions = predictions

        return Response()


class _ModelNamespace:
    def __init__(self, sdk: FakeSdk) -> None:
        self._sdk = sdk

    def __call__(self, resource_name: str) -> FakeModel:
        self._sdk.model_instance.resource_name = resource_name
        return self._sdk.model_instance

    def upload(self, **kwargs: Any) -> FakeModel:
        self._sdk.uploaded.append(kwargs)
        return FakeModel("projects/p/locations/r/models/uploaded")

    def list(self, filter: str) -> list[FakeModel]:  # noqa: A002 - SDK 名に合わせる
        return list(self._sdk.existing_models)


class _EndpointNamespace:
    def __init__(self, sdk: FakeSdk) -> None:
        self._sdk = sdk

    def list(self, filter: str) -> list[FakeEndpoint]:  # noqa: A002
        # 実 SDK は毎回新しいリストを返す。同一オブジェクトだと create() の追記が
        # 既存の参照へ漏れ、テストが実挙動とずれる。
        return list(self._sdk.existing_endpoints)

    def create(self, display_name: str) -> FakeEndpoint:
        endpoint = FakeEndpoint(display_name)
        self._sdk.created_endpoints.append(endpoint)
        self._sdk.existing_endpoints.append(endpoint)
        return endpoint


class FakeSdk:
    """google.cloud.aiplatform の最小代役。呼び出しを記録する。"""

    def __init__(
        self,
        *,
        existing_models: list[FakeModel] | None = None,
        existing_endpoints: list[FakeEndpoint] | None = None,
    ) -> None:
        self.existing_models = existing_models or []
        self.existing_endpoints = existing_endpoints or []
        self.jobs: list[FakeJob] = []
        self.uploaded: list[dict[str, Any]] = []
        self.created_endpoints: list[FakeEndpoint] = []
        self.model_instance = FakeModel()
        self.Model = _ModelNamespace(self)
        self.Endpoint = _EndpointNamespace(self)

    def CustomJob(self, **kwargs: Any) -> FakeJob:  # noqa: N802 - SDK 名に合わせる
        job = FakeJob(**kwargs)
        self.jobs.append(job)
        return job


def build(
    sink: RunSink, sdk: FakeSdk | None = None, **config_overrides: Any
) -> tuple[VertexAdapter, FakeSdk]:
    resolved = sdk or FakeSdk()
    return VertexAdapter(config(**config_overrides), sink=sink, aiplatform=resolved), resolved


def case() -> AdapterCase:
    def make(sink: RunSink) -> VertexAdapter:
        endpoint = FakeEndpoint()
        adapter, _ = build(sink, FakeSdk(existing_endpoints=[endpoint]))
        return adapter

    def make_failing(sink: RunSink) -> VertexAdapter:
        return VertexAdapter(
            config(),
            sink=sink,
            aiplatform=ExplodingClient("Permission denied on resource project example-gcp-project"),
        )

    return AdapterCase(
        platform=Platform.VERTEX,
        make=make,
        make_failing=make_failing,
        model_ref=MODEL_REF,
        artifact_uri=ARTIFACT_URI,
    )
