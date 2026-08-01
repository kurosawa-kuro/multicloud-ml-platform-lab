"""Snowflake の偽 SDK（Snowpark Session / Model Registry / モデルローダの代役）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests.fakes import AdapterCase, ExplodingClient

from core.telemetry.schemas import Platform
from core.telemetry.tracking import RunSink
from platforms.snowflake.adapter import SnowflakeAdapter, SnowflakeConfig

ARTIFACT_URI = "@MCML_DEV.ML.CODE/runs/r-1"
MODEL_VERSION = "V1"


def config(**overrides: Any) -> SnowflakeConfig:
    values: dict[str, Any] = {
        "database": "MCML_DEV",
        "schema": "ML",
        "warehouse": "MCML_DEV_WH",
    }
    values.update(overrides)
    return SnowflakeConfig(**values)


class FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def collect(self) -> list[Any]:
        return self._rows


class FakeFileOps:
    def __init__(self, *, model_written: bool = True) -> None:
        self.puts: list[dict[str, Any]] = []
        self.gets: list[str] = []
        self.model_written = model_written

    def put(
        self, local: str, stage: str, auto_compress: bool = True, overwrite: bool = False
    ) -> None:
        self.puts.append(
            {"local": local, "stage": stage, "auto_compress": auto_compress, "overwrite": overwrite}
        )

    def get(self, stage_path: str, local_dir: str) -> None:
        self.gets.append(stage_path)
        if self.model_written:
            Path(local_dir, "model.txt").write_text("tree", encoding="utf-8")


class FakeSession:
    def __init__(self, *, call_result: Any = None, model_written: bool = True) -> None:
        self.statements: list[str] = []
        self.file = FakeFileOps(model_written=model_written)
        self.call_result = (
            call_result
            if call_result is not None
            else json.dumps({"run_id": "r-1", "metrics": {"rmse": 0.4368}})
        )

    def sql(self, statement: str) -> FakeResult:
        self.statements.append(statement)
        if statement.startswith("CALL "):
            return FakeResult([(self.call_result,)])
        return FakeResult([])


class ExplodingSession:
    def sql(self, statement: str) -> FakeResult:
        raise RuntimeError("Insufficient privileges to operate on schema 'ML'")

    @property
    def file(self) -> Any:
        raise RuntimeError("Insufficient privileges to operate on stage")


class FakeModelVersion:
    def __init__(self, name: str = MODEL_VERSION) -> None:
        self.version_name = name
        self.runs: list[dict[str, Any]] = []
        self.predictions: list[float] = [4.2]

    def run(self, X: Any, function_name: str | None = None) -> Any:  # noqa: N803 - SDK 名
        self.runs.append({"rows": len(X), "function_name": function_name})
        import pandas as pd

        return pd.DataFrame({"output_feature_0": self.predictions})


class FakeModel:
    def __init__(self, version: FakeModelVersion) -> None:
        self._version = version
        self.default = version
        self.default_assignments: list[str] = []

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "default" and isinstance(value, str):
            self.__dict__.setdefault("default_assignments", []).append(value)
            return
        super().__setattr__(name, value)

    def version(self, name: str) -> FakeModelVersion:
        return self._version


class FakeRegistry:
    def __init__(self) -> None:
        self.version = FakeModelVersion()
        self.model = FakeModel(self.version)
        self.logged: list[dict[str, Any]] = []

    def log_model(self, model: Any, **kwargs: Any) -> FakeModelVersion:
        self.logged.append({"model": model, **kwargs})
        return self.version

    def get_model(self, name: str) -> FakeModel:
        return self.model


def build(
    sink: RunSink,
    session: FakeSession | None = None,
    registry: FakeRegistry | None = None,
    **config_overrides: Any,
) -> tuple[SnowflakeAdapter, FakeSession, FakeRegistry]:
    session = session or FakeSession()
    registry = registry or FakeRegistry()
    adapter = SnowflakeAdapter(
        config(**config_overrides),
        sink=sink,
        session=session,
        registry_factory=lambda _session: registry,
        model_loader=lambda path: f"booster:{path.name}",
    )
    return adapter, session, registry


def case() -> AdapterCase:
    def make(sink: RunSink) -> SnowflakeAdapter:
        adapter, _, _ = build(sink)
        adapter.model_version = MODEL_VERSION
        return adapter

    def make_failing(sink: RunSink) -> SnowflakeAdapter:
        return SnowflakeAdapter(
            config(),
            sink=sink,
            session=ExplodingSession(),
            registry_factory=lambda _session: ExplodingClient(
                "Insufficient privileges on registry"
            ),
            model_loader=lambda path: "booster",
        )

    return AdapterCase(
        platform=Platform.SNOWFLAKE,
        make=make,
        make_failing=make_failing,
        model_ref=MODEL_VERSION,
        artifact_uri=ARTIFACT_URI,
    )
