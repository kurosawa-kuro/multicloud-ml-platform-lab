"""構造化ログと middleware の検証（未テストだった 126 行）。

3基盤の Cloud Logging / CloudWatch / Azure Monitor がどれも拾える
**1行 JSON** であることが、この実装を1つで済ませている前提。
"""

from __future__ import annotations

import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.app.observability.logging_setup import (
    RequestLoggingMiddleware,
    StructuredJsonFormatter,
    configure_logging,
)


def format_record(record: logging.LogRecord) -> dict[str, object]:
    return json.loads(StructuredJsonFormatter().format(record))


def make_record(level: int = logging.INFO, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="app", level=level, pathname=__file__, lineno=1, msg="hello", args=(), exc_info=None
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


# --- StructuredJsonFormatter ---------------------------------------------


def test_severity_and_message_are_cloud_logging_compatible() -> None:
    payload = format_record(make_record(logging.WARNING))
    assert payload["severity"] == "WARNING"
    assert payload["message"] == "hello"


def test_request_fields_are_included_only_when_present() -> None:
    bare = format_record(make_record())
    assert "status" not in bare

    rich = format_record(make_record(method="GET", path="/health", status=200, latency_ms=1.5))
    assert (rich["method"], rich["path"], rich["status"]) == ("GET", "/health", 200)


def test_trace_uses_the_google_reserved_key() -> None:
    payload = format_record(make_record(trace="projects/p/traces/abc"))
    assert payload["logging.googleapis.com/trace"] == "projects/p/traces/abc"


def test_exception_is_rendered_into_the_same_line() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = make_record()
        record.exc_info = sys.exc_info()
    payload = format_record(record)
    assert "ValueError: boom" in str(payload["exception"])


# --- configure_logging ----------------------------------------------------


def test_configure_is_idempotent() -> None:
    """再呼び出しでハンドラが増えない（uvicorn のリロードで二重出力しない）。"""
    logger = configure_logging("test-idempotent")
    assert configure_logging("test-idempotent") is logger
    assert len(logger.handlers) == 1


def test_log_level_env_is_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    logger = configure_logging("test-level")
    assert logger.level == logging.DEBUG


# --- RequestLoggingMiddleware --------------------------------------------


@pytest.fixture
def app_with_middleware() -> tuple[TestClient, list[logging.LogRecord]]:
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("test-middleware")
    logger.setLevel(logging.INFO)
    logger.handlers = [Capture()]
    logger.propagate = False

    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware, logger=logger)

    @app.get("/ok")
    def ok() -> dict[str, str]:
        return {"status": "ok"}

    return TestClient(app), records


def test_one_access_log_per_request_with_latency(
    app_with_middleware: tuple[TestClient, list[logging.LogRecord]],
) -> None:
    client, records = app_with_middleware

    response = client.get("/ok")

    assert response.status_code == 200
    assert len(records) == 1
    record = records[0]
    assert record.method == "GET"  # type: ignore[attr-defined]
    assert record.path == "/ok"  # type: ignore[attr-defined]
    assert record.status == 200  # type: ignore[attr-defined]
    assert record.latency_ms >= 0  # type: ignore[attr-defined]


def test_request_id_is_echoed_back(
    app_with_middleware: tuple[TestClient, list[logging.LogRecord]],
) -> None:
    """呼び出し側が渡した x-request-id がレスポンスとログの両方に残ること。"""
    client, records = app_with_middleware

    response = client.get("/ok", headers={"x-request-id": "req-42"})

    assert response.headers["x-request-id"] == "req-42"
    assert records[0].request_id == "req-42"  # type: ignore[attr-defined]


def test_trace_header_is_resolved_with_project(
    app_with_middleware: tuple[TestClient, list[logging.LogRecord]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "example-gcp-project")
    client, records = app_with_middleware

    client.get("/ok", headers={"x-cloud-trace-context": "abc123/456;o=1"})

    assert records[0].trace == "projects/example-gcp-project/traces/abc123"  # type: ignore[attr-defined]


def test_trace_is_none_without_project(
    app_with_middleware: tuple[TestClient, list[logging.LogRecord]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GCP_PROJECT", raising=False)
    client, records = app_with_middleware

    client.get("/ok", headers={"x-cloud-trace-context": "abc123/456"})

    assert records[0].trace is None  # type: ignore[attr-defined]
