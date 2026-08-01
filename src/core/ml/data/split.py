from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from sklearn.model_selection import train_test_split


@dataclass(frozen=True)
class DatasetSplit:
    X_train: pd.DataFrame
    X_valid: pd.DataFrame
    X_test: pd.DataFrame
    y_train: pd.Series
    y_valid: pd.Series
    y_test: pd.Series


def split_train_valid_test(
    features: pd.DataFrame,
    target: pd.Series,
    *,
    seed: int,
    test_size: float = 0.2,
    valid_size: float = 0.2,
) -> DatasetSplit:
    X_trainval, X_test, y_trainval, y_test = train_test_split(
        features, target, test_size=test_size, random_state=seed
    )
    X_train, X_valid, y_train, y_valid = train_test_split(
        X_trainval, y_trainval, test_size=valid_size, random_state=seed
    )
    return DatasetSplit(
        X_train=X_train,
        X_valid=X_valid,
        X_test=X_test,
        y_train=y_train,
        y_valid=y_valid,
        y_test=y_test,
    )
