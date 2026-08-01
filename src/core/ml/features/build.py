from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from core.ml.data.load import RawDataset
from core.ml.features.schema import FeatureSchema, infer_feature_schema


@dataclass(frozen=True)
class FeatureTable:
    """Gold layer: model-ready features and reusable schema."""

    features: pd.DataFrame
    target: pd.Series
    schema: FeatureSchema


def build_gold_features(dataset: RawDataset) -> FeatureTable:
    # California Housing is already numeric and model-ready. Keep this function
    # as the single place where future feature engineering will live.
    features = dataset.features.copy()
    target = dataset.target.copy()
    return FeatureTable(
        features=features,
        target=target,
        schema=infer_feature_schema(features),
    )
