from __future__ import annotations

from core.ml.config.constants import FEATURE_COLUMNS

# 定義は load.py（load 側でも投げるため。ここからの import は従来どおり有効）
from core.ml.data.load import DatasetContractError, RawDataset

__all__ = ["DatasetContractError", "validate_silver_dataset"]


def validate_silver_dataset(dataset: RawDataset) -> None:
    """Silver layer: fail fast before feature building."""
    if dataset.features.empty:
        raise DatasetContractError("features must not be empty")
    if dataset.target.empty:
        raise DatasetContractError("target must not be empty")
    if len(dataset.features) != len(dataset.target):
        raise DatasetContractError("features and target length mismatch")
    if dataset.features.isna().any().any():
        raise DatasetContractError("features contain null values")
    if dataset.target.isna().any():
        raise DatasetContractError("target contains null values")
    missing = set(FEATURE_COLUMNS) - set(dataset.features.columns)
    if missing:
        raise DatasetContractError(f"feature columns missing: {sorted(missing)}")
