from __future__ import annotations

import lightgbm as lgb

from core.ml.data.split import DatasetSplit
from core.ml.training.params import default_lightgbm_params


def train_lightgbm_model(
    split: DatasetSplit,
    *,
    seed: int,
    num_boost_round: int = 2000,
    early_stopping_rounds: int = 50,
    overrides: dict | None = None,
) -> lgb.Booster:
    """overrides は default_lightgbm_params の後勝ちマージ。

    使った値は ml_runs.params / run.json に記録されるため、
    既定から外れた実験も後から照合できる。
    """
    params = {**default_lightgbm_params(seed), **(overrides or {})}
    train_set = lgb.Dataset(split.X_train, label=split.y_train)
    valid_set = lgb.Dataset(split.X_valid, label=split.y_valid, reference=train_set)

    return lgb.train(
        params,
        train_set,
        num_boost_round=num_boost_round,
        valid_sets=[train_set, valid_set],
        valid_names=["train", "valid"],
        callbacks=[
            lgb.early_stopping(stopping_rounds=early_stopping_rounds),
            lgb.log_evaluation(period=100),
        ],
    )
