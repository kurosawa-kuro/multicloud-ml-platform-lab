from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import pandas as pd


def save_model_artifacts(
    *,
    output_dir: Path,
    model: lgb.Booster,
    metrics: dict,
    feature_importance: pd.DataFrame,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    # best_iteration で切って保存する。全イテレーション保存だと、評価は
    # best_iteration の予測・serving は全木の予測になり、学習時メトリクスと
    # エンドポイントの応答が一致しなくなる（train/serve skew）。
    model.save_model(str(output_dir / "model.txt"), num_iteration=model.best_iteration)
    (output_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
    feature_importance.to_csv(output_dir / "feature_importance.csv", index=False)
