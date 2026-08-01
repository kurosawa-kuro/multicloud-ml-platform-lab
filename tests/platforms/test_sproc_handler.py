"""Snowflake sproc ハンドラ（warehouse の中で動く側）の検証。

session だけを偽物にし、**学習パイプラインは実際に走らせる**。
sproc_handler は「Tier B が CLI を経由せず run_training_pipeline() を直接呼ぶ」
契約の実装なので、ここをモックすると検証対象が消える。

守る不変条件:
  - source_table を session から読み、pandas に落として学習する
  - 成果物を stage へ PUT する（auto_compress なし = そのまま読める形）
  - 戻り値（VARIANT）に run_id / metrics / code_revision が載る
    （呼び出し側 adapter がこれを ml_runs に写す）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
from tests.conftest import make_sample_frame

from core.telemetry.sinks import JSONL_FILENAME
from platforms.snowflake import sproc_handler

FAST_PARAMS: dict[str, Any] = {"num_boost_round": 20, "early_stopping_rounds": 5}


class FakeTable:
    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame

    def to_pandas(self) -> pd.DataFrame:
        return self._frame


class FakeSnowparkSession:
    """sproc に渡ってくる Snowpark Session の最小代役。"""

    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame
        self.table_requests: list[str] = []
        self.puts: list[dict[str, Any]] = []

        class FileOps:
            def __init__(self, outer: FakeSnowparkSession) -> None:
                self._outer = outer

            def put(
                self,
                local: str,
                stage: str,
                auto_compress: bool = True,
                overwrite: bool = False,
            ) -> None:
                self._outer.puts.append(
                    {"local": local, "stage": stage, "auto_compress": auto_compress}
                )

        self.file = FileOps(self)

    def table(self, name: str) -> FakeTable:
        self.table_requests.append(name)
        return FakeTable(self._frame)


def test_trains_from_the_requested_table_and_returns_summary() -> None:
    session = FakeSnowparkSession(make_sample_frame())

    result = sproc_handler.main(
        session, {**FAST_PARAMS, "source_table": "CALIFORNIA_HOUSING", "run_id": "sproc-run-1"}
    )

    assert session.table_requests == ["CALIFORNIA_HOUSING"]
    assert result["run_id"] == "sproc-run-1"
    assert "rmse" in result["metrics"]
    assert result["code_revision"]


def test_uploads_all_artifacts_to_the_stage_uncompressed() -> None:
    """model.txt がそのまま読めないと register_model（Booster 復元）が壊れる。"""
    session = FakeSnowparkSession(make_sample_frame())
    stage = "@MCML_DEV.ML.CODE/runs/sproc-run-2"

    result = sproc_handler.main(session, {**FAST_PARAMS, "stage_path": stage})

    uploaded = sorted(result["uploaded"])
    assert uploaded == ["feature_importance.csv", "metrics.json", "model.txt", "run.json"]
    assert all(p["stage"] == stage for p in session.puts)
    assert all(p["auto_compress"] is False for p in session.puts)


def test_no_stage_path_skips_upload_but_still_trains() -> None:
    session = FakeSnowparkSession(make_sample_frame())

    result = sproc_handler.main(session, dict(FAST_PARAMS))

    assert result["uploaded"] == []
    assert session.puts == []
    assert "rmse" in result["metrics"]


def test_uppercase_columns_from_snowflake_are_accepted() -> None:
    """Snowflake の to_pandas() は列名を大文字で返す。正規化が効くこと。"""
    upper = make_sample_frame().rename(columns=str.upper)
    session = FakeSnowparkSession(upper)

    result = sproc_handler.main(session, dict(FAST_PARAMS))

    assert "rmse" in result["metrics"]


def test_summarize_is_json_serializable() -> None:
    session = FakeSnowparkSession(make_sample_frame())
    result = sproc_handler.main(session, dict(FAST_PARAMS))

    assert json.loads(sproc_handler.summarize(result))["run_id"] == result["run_id"]


# --- ジョブ内テレメトリ（Tier B の到達経路）------------------------------


def test_ml_runs_jsonl_is_uploaded_to_the_stage() -> None:
    """warehouse から Neon へは届かない（psycopg が Anaconda channel に無い）。

    そのため ml_runs 1行を JSONL で stage へ出し、`make collect` で回収する。
    **これを出さないと Tier B の run が Neon に一切現れない。**
    """
    session = FakeSnowparkSession(make_sample_frame())
    stage = "@MCML_DEV.ML.CODE/runs/sproc-run-3"

    result = sproc_handler.main(
        session, {**FAST_PARAMS, "stage_path": stage, "run_id": "sproc-run-3", "attempt": 4}
    )

    assert result["write_path"] == "collected"
    uploads = [p for p in session.puts if p["local"].endswith(JSONL_FILENAME)]
    assert len(uploads) == 1
    assert uploads[0]["stage"] == stage
    assert uploads[0]["auto_compress"] is False


def test_uploaded_run_row_carries_the_comparison_keys(tmp_path: Path) -> None:
    """回収された行がそのまま比較 SELECT に乗ること（run_id / attempt / metrics）。"""
    captured: list[Path] = []

    class CapturingSession(FakeSnowparkSession):
        def __init__(self, frame: pd.DataFrame) -> None:
            super().__init__(frame)
            outer = self

            class FileOps:
                def put(
                    self,
                    local: str,
                    stage: str,
                    auto_compress: bool = True,
                    overwrite: bool = False,
                ) -> None:
                    outer.puts.append({"local": local, "stage": stage})
                    path = Path(local.removeprefix("file://"))
                    if path.name == JSONL_FILENAME:
                        # sproc は一時ディレクトリを消すのでここで内容を退避する
                        copy = tmp_path / JSONL_FILENAME
                        copy.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
                        captured.append(copy)

            self.file = FileOps()

    session = CapturingSession(make_sample_frame())
    sproc_handler.main(
        session, {**FAST_PARAMS, "stage_path": "@S/runs/x", "run_id": "sproc-run-4", "attempt": 2}
    )

    assert captured, "ml_runs.jsonl が PUT されていない"
    record = json.loads(captured[0].read_text(encoding="utf-8"))
    assert record["run_id"] == "sproc-run-4"
    assert record["platform"] == "snowflake"
    assert (record["tier"], record["unification_unit"]) == ("B", "package")
    assert record["attempt"] == 2
    assert record["write_path"] == "collected"
    assert record["status"] == "success"
    assert "rmse" in record["metrics"]
    assert record["code_revision"]


def test_telemetry_failure_does_not_fail_the_training() -> None:
    """記録に失敗しても学習の結果を壊さない（06_error_policy「telemetry は非致命」）。"""

    class BrokenTelemetrySession(FakeSnowparkSession):
        def __init__(self, frame: pd.DataFrame) -> None:
            super().__init__(frame)
            outer = self

            class FileOps:
                def put(
                    self,
                    local: str,
                    stage: str,
                    auto_compress: bool = True,
                    overwrite: bool = False,
                ) -> None:
                    if local.endswith(JSONL_FILENAME):
                        raise RuntimeError("stage write denied")
                    outer.puts.append({"local": local, "stage": stage})

            self.file = FileOps()

    result = sproc_handler.main(
        BrokenTelemetrySession(make_sample_frame()), {**FAST_PARAMS, "stage_path": "@S/runs/y"}
    )

    assert "rmse" in result["metrics"]
    assert result["write_path"] is None  # 記録できなかった事実は残す
    assert "model.txt" in result["uploaded"]
