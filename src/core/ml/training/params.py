from __future__ import annotations


def default_lightgbm_params(seed: int) -> dict:
    return {
        "objective": "regression",
        "metric": "rmse",
        "learning_rate": 0.05,
        "num_leaves": 63,
        "feature_fraction": 0.9,
        "bagging_fraction": 0.9,
        "bagging_freq": 5,
        "min_data_in_leaf": 20,
        "verbose": -1,
        "seed": seed,
        # 5基盤で同一メトリクスが再現される必要があるため決定的モードを固定する
        "deterministic": True,
        "force_row_wise": True,
    }
