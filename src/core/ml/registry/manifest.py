from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path


def new_run_id() -> str:
    """uuid4 文字列。

    ml_runs.run_id が uuid 型のため、timestamp 形式だと INSERT で落ちる。
    artifact（run.json）と ml_runs で**同じ id** を使い、1 run = 1 id で
    突合できることが重要。時刻は created_at が持つ。
    """
    return str(uuid.uuid4())


def build_run_manifest(
    *,
    run_id: str,
    code_revision: str,
    model_name: str,
    dataset_name: str,
    seed: int,
    metrics: dict,
    params: dict,
    artifact_uri: str | None,
    feature_columns: list[str],
    feature_dtypes: dict[str, str],
) -> dict:
    return {
        "run_id": run_id,
        # ml_runs.code_revision と同値。5基盤で一致しなければ比較が無効になるため、
        # artifact 側にも必ず残す（後からどの run がどのコードか照合できるように）。
        "code_revision": code_revision,
        "model": model_name,
        "dataset": dataset_name,
        "seed": seed,
        "metrics": metrics,
        # 既定と違うパラメータで走った事実を artifact 側にも残す
        "params": params,
        "artifact_uri": artifact_uri,
        "feature_schema": {
            "columns": feature_columns,
            "dtypes": feature_dtypes,
        },
        "created_at": datetime.now(UTC).isoformat(),
    }


def write_run_manifest(output_dir: Path, payload: dict) -> Path:
    path = output_dir / "run.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return path
