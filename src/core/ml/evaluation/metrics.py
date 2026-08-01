from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score


def evaluate_regression(
    model: lgb.Booster,
    X_test: pd.DataFrame,
    y_test: pd.Series,
) -> dict[str, float | int]:
    y_pred = model.predict(X_test, num_iteration=model.best_iteration)
    return {
        "rmse": float(np.sqrt(mean_squared_error(y_test, y_pred))),
        "mae": float(mean_absolute_error(y_test, y_pred)),
        "r2": float(r2_score(y_test, y_pred)),
        "best_iteration": int(model.best_iteration),
    }
