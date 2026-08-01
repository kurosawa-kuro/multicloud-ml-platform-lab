"""Snowflake stored procedure のハンドラ（**warehouse の中で動くコード**）。

Tier A の entrypoint シム（docker/training/entrypoint_*.sh）に相当する。
違いはコンテナではなく **stage へ上げたパッケージを import して呼ばれる**こと。

呼ばれ方（adapter が DDL で宣言する）:

    CREATE OR REPLACE PROCEDURE <db>.<schema>.<proc>(PARAMS VARIANT)
      RETURNS VARIANT
      LANGUAGE PYTHON
      RUNTIME_VERSION = '3.12'
      PACKAGES = ('snowflake-snowpark-python', 'lightgbm', 'scikit-learn', 'pandas')
      IMPORTS = ('@<stage>/dist/<package>.zip')
      HANDLER = 'platforms.snowflake.sproc_handler.main'

**CLI を経由しない**（docs/02_architecture.md の Tier B 契約）。
`core.ml.cli` は Tier A のコンテナ用で、ここは `run_training_pipeline()` を直接呼ぶ。

依存は Anaconda channel にあるものだけ（psycopg は無い）ため、**warehouse 内から
Neon へ直接書けない**。そこで ml_runs 1行を JSONL として成果物と同じ stage へ出し、
`make collect`（scripts/collect_jsonl.py）でローカルから流し込む。
その行は write_path='collected' で入る —— Tier B が宣言的オブジェクト経由でしか
外へ出られないことの一次データであり、**到達できないこと自体が比較結果**
（docs/01_requirements.md 破綻条件 / docs/02_architecture.md「Neon 集約」）。

core.telemetry.sinks は stdlib のみなので warehouse でも必ず import できる。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

# Snowflake の warehouse ランタイムでは stage からこのパッケージが import される。
# ローカル（adapter 側）では import されない = SDK 依存をここに持ち込まない。
from core.ml.pipelines.train_pipeline import TrainingConfig, run_training_pipeline
from core.telemetry.schemas import (
    FailureClass,
    MlRun,
    Platform,
    Stage,
    Status,
    WritePath,
)
from core.telemetry.sinks import JSONL_FILENAME, JsonlRunSink
from core.telemetry.tracking import PLATFORM_TIER

# 学習入力テーブル。sklearn 版 California Housing のみを使う
# （Kaggle 版は列も目的変数スケールも別物。Snowflake 事前調査で判明した罠）。
DEFAULT_SOURCE_TABLE = "CALIFORNIA_HOUSING"


def main(session: Any, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """warehouse 内で学習を1回実行し、成果物を stage へ置いて要約を返す。

    Args:
        session: Snowpark Session（Snowflake が渡す）
        params: ハイパーパラメータ + 実行設定（source_table / stage_path / run_id）

    Returns:
        run_id / metrics / artifact_stage_path を含む dict（VARIANT として返る）
    """
    settings = dict(params or {})
    source_table = str(settings.pop("source_table", DEFAULT_SOURCE_TABLE))
    stage_path = str(settings.pop("stage_path", ""))
    run_id = settings.pop("run_id", None)
    attempt = int(settings.pop("attempt", 1))

    df = session.table(source_table).to_pandas()

    result = run_training_pipeline(
        df,
        TrainingConfig(run_id=str(run_id) if run_id else None, params=settings),
    )

    uploaded: list[str] = []
    telemetry_path: str | None = None
    if stage_path:
        uploaded = _put_artifacts(session, result.output_dir, stage_path)
        telemetry_path = _put_run_record(session, result, stage_path, attempt=attempt)

    return {
        "run_id": result.run_id,
        "code_revision": result.code_revision,
        "metrics": result.metrics,
        "artifact_stage_path": stage_path,
        "uploaded": uploaded,
        # 到達経路。Tier B は必ず collected（warehouse から Neon へは届かない）
        "write_path": WritePath.COLLECTED.value if telemetry_path else None,
    }


def _put_run_record(session: Any, result: Any, stage_path: str, *, attempt: int) -> str | None:
    """ml_runs 1行を JSONL にして stage へ置く（`make collect` が回収する）。

    **telemetry の失敗で学習を落とさない**（docs/06_error_policy.md）。
    ここで例外を投げると、成功した学習が失敗として記録されてしまう。
    """
    tier, unification_unit = PLATFORM_TIER[Platform.SNOWFLAKE]
    run = MlRun(
        run_id=result.run_id,
        platform=Platform.SNOWFLAKE,
        tier=tier,
        unification_unit=unification_unit,
        stage=Stage.TRAIN,
        status=Status.SUCCESS,
        attempt=attempt,
        failure_class=FailureClass.NONE,
        code_revision=result.code_revision,
        write_path=WritePath.COLLECTED,
        metrics=dict(result.metrics),
    )
    try:
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp)
            JsonlRunSink(local).record_run(run)
            session.file.put(
                f"file://{local / JSONL_FILENAME}",
                stage_path,
                auto_compress=False,
                overwrite=True,
            )
    except Exception as exc:  # noqa: BLE001 - telemetry は非致命
        print(f"sproc_handler: ml_runs の JSONL 退避に失敗: {exc}")
        return None
    return f"{stage_path}/{JSONL_FILENAME}"


def _put_artifacts(session: Any, output_dir: Path, stage_path: str) -> list[str]:
    """成果物を stage へ PUT する。

    warehouse からの PUT は `file://` のローカルパス指定。auto_compress は切る
    （model.txt がそのまま読めないと、他基盤と同じ成果物として比較できない）。
    """
    uploaded = []
    for artifact in sorted(output_dir.iterdir()):
        if artifact.is_dir():
            continue
        session.file.put(
            f"file://{artifact}",
            stage_path,
            auto_compress=False,
            overwrite=True,
        )
        uploaded.append(artifact.name)
    return uploaded


def summarize(result: dict[str, Any]) -> str:
    """CALL の戻り（VARIANT）をログ用の1行にする。"""
    return json.dumps(result, ensure_ascii=False, default=str)
