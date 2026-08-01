"""テスト共通の土台。

ここに置くのは **5基盤で同じもの** だけ:
  - `sink` フィクスチャ（JSONL への記録先）。5ファイルで同じ定義を書いていたのを集約
  - 記録された run を読み戻すヘルパ（「必ず1行残る」の検証に毎回必要）
  - `scripts/` 直下のモジュールを読むヘルパ（パッケージではないので importlib 経由）

基盤ごとの偽 SDK は `tests/fakes/` に置く（ここには置かない。
基盤差はテストで見たいものであって、共通化して隠すものではない）。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from core.ml.config.constants import FEATURE_COLUMNS, TARGET_COLUMN
from core.telemetry.sinks import JsonlRunSink

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"


@pytest.fixture
def sink(tmp_path: Path) -> JsonlRunSink:
    """1テスト1ファイルの記録先。attempt の数え上げも tmp ごとに独立する。"""
    return JsonlRunSink(tmp_path)


@pytest.fixture
def recorded(sink: JsonlRunSink) -> Any:
    """sink に書かれた行を読み戻す。

    「失敗しても必ず1行残る」を確かめるテストが5基盤ぶんあるので、
    JSONL の読み方をここに1つだけ持つ。
    """

    def read() -> list[dict[str, Any]]:
        if not sink.path.exists():
            return []
        return [
            json.loads(line)
            for line in sink.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    return read


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


def make_sample_frame(rows: int = 200, seed: int = 0) -> pd.DataFrame:
    """入力契約（FEATURE_COLUMNS + target + row_id）を満たす合成データ。

    実データ（parquet）が無い CI でも学習コードを**実際に走らせる**ための素材。
    test_core_ml_pipeline / CLI / sproc のテストが共有する。
    """
    rng = np.random.default_rng(seed)
    data: dict[str, np.ndarray] = {col: rng.normal(size=rows) for col in FEATURE_COLUMNS}
    # 目的変数は特徴量に依存させる（無相関だと early stopping が即発火して
    # best_iteration=1 になり、モデル切り詰めの検証にならない）
    data[TARGET_COLUMN] = data[FEATURE_COLUMNS[0]] * 2.0 + rng.normal(scale=0.1, size=rows)
    data["row_id"] = np.arange(rows)
    return pd.DataFrame(data)


@pytest.fixture
def sample_frame() -> pd.DataFrame:
    return make_sample_frame()


@pytest.fixture
def sample_parquet(tmp_path: Path) -> Path:
    """合成データの parquet。CLI テスト（--input はディレクトリ）用。"""
    directory = tmp_path / "input"
    directory.mkdir()
    make_sample_frame().to_parquet(directory / "part-0.parquet", index=False)
    return directory


def load_script(name: str) -> Any:
    """`scripts/<name>.py` を読む。

    scripts/ はパッケージではない（CLI 置き場）ので通常の import ができない。
    テストから叩く経路をここに1本化する。
    """
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    if spec is None or spec.loader is None:  # pragma: no cover - パス誤りのみ
        raise ImportError(f"scripts/{name}.py を読み込めない")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
