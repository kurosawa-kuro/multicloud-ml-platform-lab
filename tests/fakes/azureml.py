"""Azure ML の偽 SDK（MLClient と azure.ai.ml エンティティの代役）。"""

from __future__ import annotations

import json
from typing import Any

from tests.fakes import AdapterCase, ExplodingNamespace, FakePoller, Recorded
from tests.fakes import RecordingFactory as FakeEntities

from core.telemetry.schemas import Platform
from core.telemetry.tracking import RunSink
from platforms.azureml.adapter import AzureMLAdapter, AzureMlConfig

ARTIFACT_URI = "azureml://jobs/mcml-job-1/outputs/model"
MODEL_REF = "mcml-california-housing:3"


def config(**overrides: Any) -> AzureMlConfig:
    values: dict[str, Any] = {
        "subscription_id": "00000000-0000-0000-0000-000000000000",
        "resource_group": "mcml-dev-rg",
        "workspace_name": "mcml-dev-ws-abc123",
        "compute_cluster": "mcml-dev-cpu",
        "training_image_uri": "mcmldevacr.azurecr.io/training:abc",
        "serving_image_uri": "mcmldevacr.azurecr.io/serving:abc",
    }
    values.update(overrides)
    return AzureMlConfig(**values)


class FakeJobs:
    def __init__(self, status: str = "Completed") -> None:
        self.status = status
        self.submitted: list[Recorded] = []
        self.streamed: list[str] = []

    def create_or_update(self, job: Recorded) -> Any:
        self.submitted.append(job)
        job.name = "mcml-job-1"
        return job

    def stream(self, name: str) -> None:
        self.streamed.append(name)

    def get(self, name: str) -> Any:
        class Finished:
            status = self.status

        return Finished()


class FakeModels:
    def __init__(self) -> None:
        self.created: list[Recorded] = []

    def create_or_update(self, model: Recorded) -> Any:
        self.created.append(model)

        class Registered:
            version = "3"

        return Registered()


class FakeOnlineEndpoints:
    def __init__(self, *, exists: bool = False) -> None:
        self.exists = exists
        self.upserts: list[Recorded] = []
        self.deleted: list[str] = []
        self.invocations: list[dict[str, Any]] = []
        self.response = json.dumps({"predictions": [4.2]})

    def begin_create_or_update(self, endpoint: Recorded) -> FakePoller:
        self.upserts.append(endpoint)
        self.exists = True
        return FakePoller(endpoint)

    def get(self, name: str) -> Any:
        if not self.exists:
            raise RuntimeError(f"ResourceNotFound: {name}")
        return {"name": name}

    def begin_delete(self, name: str) -> FakePoller:
        self.deleted.append(name)
        self.exists = False
        return FakePoller(None)

    def invoke(self, **kwargs: Any) -> str:
        self.invocations.append(kwargs)
        return self.response


class FakeOnlineDeployments:
    def __init__(self) -> None:
        self.upserts: list[Recorded] = []

    def begin_create_or_update(self, deployment: Recorded) -> FakePoller:
        self.upserts.append(deployment)
        return FakePoller(deployment)


class FakeMlClient:
    def __init__(self, *, job_status: str = "Completed", endpoint_exists: bool = False) -> None:
        self.jobs = FakeJobs(job_status)
        self.models = FakeModels()
        self.online_endpoints = FakeOnlineEndpoints(exists=endpoint_exists)
        self.online_deployments = FakeOnlineDeployments()


def build(
    sink: RunSink, client: FakeMlClient | None = None, **config_overrides: Any
) -> tuple[AzureMLAdapter, FakeMlClient, FakeEntities]:
    client = client or FakeMlClient()
    entities = FakeEntities()
    adapter = AzureMLAdapter(
        config(**config_overrides), sink=sink, ml_client=client, entities=entities
    )
    return adapter, client, entities


def case() -> AdapterCase:
    def make(sink: RunSink) -> AzureMLAdapter:
        adapter, _, _ = build(sink, FakeMlClient(endpoint_exists=True))
        return adapter

    def make_failing(sink: RunSink) -> AzureMLAdapter:
        return AzureMLAdapter(
            config(),
            sink=sink,
            ml_client=ExplodingNamespace("AuthorizationFailed: no permission"),
            entities=FakeEntities(),
        )

    return AdapterCase(
        platform=Platform.AZUREML,
        make=make,
        make_failing=make_failing,
        model_ref=MODEL_REF,
        artifact_uri=ARTIFACT_URI,
    )
