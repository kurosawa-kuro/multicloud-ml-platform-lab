"""JSONL sink（fallback 経路）の契約。

Neon へ直接届けられない基盤でも計測が失われないこと、
回収時に write_path='collected' で区別できることを守る。
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.fakes.telemetry import make_run

from core.telemetry.schemas import FailureClass, MlRun, Platform, Stage, WritePath
from core.telemetry.sinks import JsonlRunSink, record_to_run, run_to_record


def _run(run_id: str = "00000000-0000-0000-0000-000000000001") -> MlRun:
    # ファクトリは fakes/telemetry.py に集約。platform で tier / unit が追従する
    return make_run(run_id=run_id, platform=Platform.SNOWFLAKE, metrics={"rmse": 0.43})


def test_jsonl_sink_stamps_collected(sink: JsonlRunSink, tmp_path: Path) -> None:
    """JSONL に落ちた行は collect 経由でしか Neon に届かない = collected 確定。"""
    returned = sink.record_run(_run())
    assert returned is WritePath.COLLECTED

    line = json.loads(sink.path.read_text(encoding="utf-8").strip())
    assert line["write_path"] == "collected"


def test_jsonl_roundtrip(sink: JsonlRunSink, tmp_path: Path) -> None:
    """JSONL → MlRun の復元が劣化しないこと（collect の前提）。"""
    original = _run()
    original.failure_class = FailureClass.NETWORK
    original.error_excerpt = "connection timeout"
    restored = record_to_run(run_to_record(original))
    assert restored.platform is Platform.SNOWFLAKE
    assert restored.failure_class is FailureClass.NETWORK
    assert restored.metrics == {"rmse": 0.43}
    assert restored.code_revision == "a" * 40


def test_jsonl_next_attempt_counts_same_platform_stage(sink: JsonlRunSink, tmp_path: Path) -> None:
    assert sink.next_attempt(Platform.SNOWFLAKE, Stage.TRAIN) == 1
    sink.record_run(_run("00000000-0000-0000-0000-000000000002"))
    sink.record_run(_run("00000000-0000-0000-0000-000000000003"))
    assert sink.next_attempt(Platform.SNOWFLAKE, Stage.TRAIN) == 3
    assert sink.next_attempt(Platform.SNOWFLAKE, Stage.DEPLOY) == 1
