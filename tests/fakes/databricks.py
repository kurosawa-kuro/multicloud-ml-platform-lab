"""Databricks の偽 SDK（WorkspaceClient と serving 入力型の代役）。"""

from __future__ import annotations

from typing import Any

from tests.fakes import AdapterCase, ExplodingNamespace
from tests.fakes import RecordingFactory as FakeEntities

from core.telemetry.schemas import Platform
from core.telemetry.tracking import RunSink
from platforms.databricks.adapter import DatabricksAdapter, DatabricksConfig

MODEL_ARTIFACT = "/Volumes/mcml_dev/ml/artifacts/runs/4242/model"
MODEL_VERSION = "7"


def config(**overrides: Any) -> DatabricksConfig:
    values: dict[str, Any] = {"catalog": "mcml_dev", "schema": "ml"}
    values.update(overrides)
    return DatabricksConfig(**values)


class FakeState:
    def __init__(self, result_state: str | None) -> None:
        self.result_state = result_state
        self.state_message = "run failed: permission denied on catalog mcml_dev"


class FakeRun:
    def __init__(self, result_state: str | None = "SUCCESS") -> None:
        self.run_id = 4242
        self.state = FakeState(result_state)


class FakeWaiter:
    def __init__(self, run: FakeRun) -> None:
        self._run = run

    def result(self) -> FakeRun:
        return self._run


class FakeJob:
    def __init__(self, job_id: int, name: str) -> None:
        self.job_id = job_id
        self.settings = {"name": name}


class FakeJobs:
    def __init__(self, jobs: list[FakeJob] | None = None, result_state: str = "SUCCESS") -> None:
        self.jobs = jobs if jobs is not None else [FakeJob(101, "mcml_dev_train")]
        self.result_state = result_state
        self.run_now_calls: list[dict[str, Any]] = []
        self.list_calls: list[str] = []
        # 登録ジョブが成功すると UC に版が増える、という実挙動の代役
        self.on_register: Any = None

    def list(self, name: str) -> list[FakeJob]:
        self.list_calls.append(name)
        return self.jobs

    def run_now(self, **kwargs: Any) -> FakeWaiter:
        self.run_now_calls.append(kwargs)
        params = list(kwargs.get("python_params") or [])
        if self.result_state == "SUCCESS" and "register" in params and self.on_register:
            self.on_register()
        return FakeWaiter(FakeRun(self.result_state))


class FakeModelVersions:
    """UC の版は **SDK では作れない**（get/list/update/delete のみ）。

    実 SDK に `create` が無いことがそのまま「登録はジョブ内の MLflow で行う」
    という設計の根拠なので、fake にも生やさない。
    """

    def __init__(self, versions: list[int] | None = None) -> None:
        self.versions = versions if versions is not None else [int(MODEL_VERSION) - 1]
        self.list_calls: list[str] = []
        self.deleted: list[tuple[str, int]] = []

    def list(self, full_name: str) -> list[Any]:
        self.list_calls.append(full_name)
        return [type("V", (), {"version": v})() for v in self.versions]

    def delete(self, full_name: str, version: int) -> None:
        self.deleted.append((full_name, version))
        self.versions.remove(version)


class FakeServingEndpoints:
    def __init__(self, *, exists: bool = False) -> None:
        self.exists = exists
        self.created: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self.queries: list[dict[str, Any]] = []
        self.predictions: list[float] = [4.2]

    def get(self, name: str) -> Any:
        if not self.exists:
            raise RuntimeError(f"RESOURCE_DOES_NOT_EXIST: {name}")
        return {"name": name}

    def create_and_wait(self, **kwargs: Any) -> Any:
        self.created.append(kwargs)
        self.exists = True
        return {"name": kwargs.get("name")}

    def update_config_and_wait(self, **kwargs: Any) -> Any:
        self.updated.append(kwargs)
        return {"name": kwargs.get("name")}

    def delete(self, name: str) -> None:
        self.deleted.append(name)
        self.exists = False

    def query(self, **kwargs: Any) -> Any:
        self.queries.append(kwargs)
        predictions = self.predictions

        class Response:
            def __init__(self) -> None:
                self.predictions = predictions

        return Response()


class FakeFiles:
    def __init__(self) -> None:
        self.uploads: list[str] = []

    def upload(self, path: str, contents: Any, overwrite: bool = False) -> None:
        self.uploads.append(path)


class FakeCurrentUser:
    """MLflow 実験のパス解決に使う（wheel task には既定の実験が無い）。"""

    @staticmethod
    def me() -> Any:
        return type("Me", (), {"user_name": "owner@example.com"})()


class FakeWorkspaceClient:
    def __init__(
        self,
        *,
        result_state: str = "SUCCESS",
        endpoint_exists: bool = False,
        jobs: list[FakeJob] | None = None,
        registers_version: bool = True,
    ) -> None:
        self.jobs = FakeJobs(jobs, result_state)
        self.model_versions = FakeModelVersions(versions=[])
        self.serving_endpoints = FakeServingEndpoints(exists=endpoint_exists)
        self.files = FakeFiles()
        self.current_user = FakeCurrentUser()
        if registers_version:
            self.jobs.on_register = lambda: self.model_versions.versions.append(int(MODEL_VERSION))


def build(
    sink: RunSink, client: FakeWorkspaceClient | None = None, **config_overrides: Any
) -> tuple[DatabricksAdapter, FakeWorkspaceClient, FakeEntities]:
    client = client or FakeWorkspaceClient()
    entities = FakeEntities()
    adapter = DatabricksAdapter(
        config(**config_overrides), sink=sink, workspace_client=client, entities=entities
    )
    return adapter, client, entities


def case() -> AdapterCase:
    def make(sink: RunSink) -> DatabricksAdapter:
        adapter, _, _ = build(sink, FakeWorkspaceClient(endpoint_exists=True))
        return adapter

    def make_failing(sink: RunSink) -> DatabricksAdapter:
        return DatabricksAdapter(
            config(),
            sink=sink,
            workspace_client=ExplodingNamespace("PERMISSION_DENIED: no permission"),
            entities=FakeEntities(),
        )

    return AdapterCase(
        platform=Platform.DATABRICKS,
        make=make,
        make_failing=make_failing,
        model_ref=MODEL_VERSION,
        artifact_uri=MODEL_ARTIFACT,
    )
