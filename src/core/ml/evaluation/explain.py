from __future__ import annotations

import lightgbm as lgb
import pandas as pd


def build_feature_importance(
    model: lgb.Booster,
    feature_names: list[str],
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "feature": feature_names,
            "gain": model.feature_importance(importance_type="gain"),
            "split": model.feature_importance(importance_type="split"),
        }
    ).sort_values("gain", ascending=False)
