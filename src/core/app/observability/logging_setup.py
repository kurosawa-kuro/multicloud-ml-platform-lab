"""構造化ログ + リクエスト middleware。

流用元: private-ops starter-kit `api-fastapi/src/micropost_api/gcp_logging.py`。
**変更点**: google-cloud-logging クライアント分岐を削除した。

理由: 5基盤共通のイメージに GCP SDK を入れない。stdout への JSON は
Cloud Logging / CloudWatch / Azure Monitor いずれも構造化ログとして拾うため、
1つの実装で3基盤を賄える。ログ形式の差自体は比較対象ではない。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_SEVERITY_MAP = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRITICAL",
}


class StructuredJsonFormatter(logging.Formatter):
    """Cloud Logging-compatible JSON formatter."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "severity": _SEVERITY_MAP.get(record.levelno, record.levelname),
            "message": record.getMessage(),
            "logger": record.name,
        }
        trace = getattr(record, "trace", None)
        if trace:
            payload["logging.googleapis.com/trace"] = trace
        for key in ("method", "path", "status", "latency_ms", "request_id"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(logger_name: str = "app") -> logging.Logger:
    logger = logging.getLogger(logger_name)
    if getattr(logger, "_log_configured", False):
        return logger

    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger.setLevel(level)
    logger.handlers.clear()

    handler: logging.Handler = logging.StreamHandler(sys.stdout)
    if os.getenv("LOG_FORMAT", "json").lower() == "json":
        handler.setFormatter(StructuredJsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s"))

    logger.addHandler(handler)
    logger.propagate = False
    logger._log_configured = True  # type: ignore[attr-defined]
    return logger


def _extract_trace(request: Request) -> str | None:
    project = os.getenv("GCP_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT")
    header = request.headers.get("x-cloud-trace-context")
    if not project or not header:
        return None
    trace_id = header.split("/", 1)[0]
    if not trace_id:
        return None
    return f"projects/{project}/traces/{trace_id}"


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Emit one structured access log per request."""

    def __init__(self, app, logger: logging.Logger):
        super().__init__(app)
        self._logger = logger

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        trace = _extract_trace(request)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self._logger.exception(
                "request failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "latency_ms": round(elapsed_ms, 2),
                    "request_id": request_id,
                    "trace": trace,
                },
            )
            raise
        elapsed_ms = (time.perf_counter() - start) * 1000
        response.headers["x-request-id"] = request_id
        self._logger.info(
            "request completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "latency_ms": round(elapsed_ms, 2),
                "request_id": request_id,
                "trace": trace,
            },
        )
        return response
