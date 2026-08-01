from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class FeatureSchema:
    columns: list[str]
    dtypes: dict[str, str]


def infer_feature_schema(features: pd.DataFrame) -> FeatureSchema:
    return FeatureSchema(
        columns=list(features.columns),
        dtypes={column: str(dtype) for column, dtype in features.dtypes.items()},
    )
