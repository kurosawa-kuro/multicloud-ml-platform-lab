"""core/ml の TDD contract。

比較が成立する前提（同一データ・同一seed で同一メトリクス）を機械で守る。
対象は starter-kit から移植した Bronze → Silver → Gold の各層。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.ml.config.constants import FEATURE_COLUMNS, RANDOM_SEED, TARGET_COLUMN
from core.ml.data.load import RawDataset, load_bronze_dataset
from core.ml.data.split import split_train_valid_test
from core.ml.data.validate import DatasetContractError, validate_silver_dataset
from core.ml.features.build import build_gold_features


@pytest.fixture
def sample() -> pd.DataFrame:
    """契約を満たす最小データ。seed 固定で生成し、テスト間で同一にする。"""
    rng = np.random.default_rng(0)
    n = 200
    data: dict[str, np.ndarray] = {col: rng.normal(size=n) for col in FEATURE_COLUMNS}
    data[TARGET_COLUMN] = rng.normal(size=n)
    data["row_id"] = np.arange(n)
    return pd.DataFrame(data)


def _raw(df: pd.DataFrame) -> RawDataset:
    return load_bronze_dataset(df)


# --- Bronze: 入力の受け取り ------------------------------------------------


def test_load_keeps_only_contract_columns(sample: pd.DataFrame) -> None:
    raw = _raw(sample)
    assert tuple(raw.features.columns) == FEATURE_COLUMNS
    assert "row_id" not in raw.features.columns


def test_load_is_row_order_independent(sample: pd.DataFrame) -> None:
    """行順が変わっても同じ結果になること。

    基盤ごとに DataFrame の作り方が違う（read_parquet / to_pandas / toPandas）ため、
    行順に依存すると同じ seed でも基盤間で分割がずれ、metric parity が落ちる。
    """
    shuffled = sample.sample(frac=1.0, random_state=7)
    pd.testing.assert_frame_equal(_raw(sample).features, _raw(shuffled).features)


# --- Silver: fail-fast の契約 ---------------------------------------------


def test_validate_accepts_contract_data(sample: pd.DataFrame) -> None:
    validate_silver_dataset(_raw(sample))


def test_validate_rejects_empty() -> None:
    empty = pd.DataFrame({c: [] for c in (*FEATURE_COLUMNS, TARGET_COLUMN)})
    with pytest.raises(DatasetContractError):
        validate_silver_dataset(_raw(empty))


def test_validate_rejects_missing_column(sample: pd.DataFrame) -> None:
    raw = _raw(sample.drop(columns=[FEATURE_COLUMNS[0]]))
    with pytest.raises(DatasetContractError, match="feature columns missing"):
        validate_silver_dataset(raw)


def test_validate_rejects_nulls(sample: pd.DataFrame) -> None:
    broken = sample.copy()
    broken.loc[0, FEATURE_COLUMNS[0]] = np.nan
    with pytest.raises(DatasetContractError, match="null"):
        validate_silver_dataset(_raw(broken))


def test_validate_rejects_length_mismatch(sample: pd.DataFrame) -> None:
    raw = _raw(sample)
    broken = RawDataset(
        features=raw.features,
        target=raw.target.iloc[:-1],
        feature_names=raw.feature_names,
    )
    with pytest.raises(DatasetContractError, match="length mismatch"):
        validate_silver_dataset(broken)


# --- Gold: 特徴量スキーマ --------------------------------------------------


def test_gold_schema_fixes_column_order(sample: pd.DataFrame) -> None:
    """列順が入力任せにならないこと（LightGBM に渡る並びが基盤間でずれない）。"""
    reversed_input = sample[list(reversed(sample.columns))]
    gold = build_gold_features(_raw(reversed_input))
    assert tuple(gold.schema.columns) == FEATURE_COLUMNS


def test_gold_excludes_target(sample: pd.DataFrame) -> None:
    gold = build_gold_features(_raw(sample))
    assert TARGET_COLUMN not in gold.features.columns
    assert len(gold.target) == len(sample)


# --- split: 再現性の契約 ---------------------------------------------------


def test_split_is_deterministic(sample: pd.DataFrame) -> None:
    gold = build_gold_features(_raw(sample))
    a = split_train_valid_test(gold.features, gold.target, seed=RANDOM_SEED)
    b = split_train_valid_test(gold.features, gold.target, seed=RANDOM_SEED)
    pd.testing.assert_frame_equal(a.X_train, b.X_train)
    pd.testing.assert_series_equal(a.y_test, b.y_test)


def test_split_covers_all_rows_without_overlap(sample: pd.DataFrame) -> None:
    gold = build_gold_features(_raw(sample))
    s = split_train_valid_test(gold.features, gold.target, seed=RANDOM_SEED)
    assert len(s.X_train) + len(s.X_valid) + len(s.X_test) == len(sample)
    indices = set(s.X_train.index) | set(s.X_valid.index) | set(s.X_test.index)
    assert len(indices) == len(sample)


# --- Snowflake 大文字列名の正規化 ------------------------------------------


def test_uppercase_columns_are_normalized(sample: pd.DataFrame) -> None:
    """Snowflake の `session.table(...).to_pandas()` は識別子を大文字で返す。

    正規化が無いと Tier B だけ列欠落で落ちる。
    """
    upper = sample.rename(columns={c: c.upper() for c in sample.columns})
    raw = load_bronze_dataset(upper)
    assert tuple(raw.features.columns) == FEATURE_COLUMNS
    validate_silver_dataset(raw)


def test_normalization_does_not_change_results(sample: pd.DataFrame) -> None:
    """大文字入力と小文字入力で同じ結果になること（metric parity の前提）。"""
    upper = sample.rename(columns={c: c.upper() for c in sample.columns})
    pd.testing.assert_frame_equal(
        load_bronze_dataset(sample).features, load_bronze_dataset(upper).features
    )


def test_missing_row_id_rejected(sample: pd.DataFrame) -> None:
    """row_id 無しを許すと行順が基盤依存になり parity が黙って壊れる。"""
    with pytest.raises(DatasetContractError, match="row_id"):
        load_bronze_dataset(sample.drop(columns=["row_id"]))


def test_duplicate_row_id_rejected(sample: pd.DataFrame) -> None:
    broken = sample.copy()
    broken.loc[1, "row_id"] = broken.loc[0, "row_id"]
    with pytest.raises(DatasetContractError, match="row_id"):
        load_bronze_dataset(broken)


def test_colliding_columns_after_normalize_rejected(sample: pd.DataFrame) -> None:
    """MED_INC と med_inc の併存を黙って上書きしない。"""
    broken = sample.copy()
    broken["MED_INC"] = 0.0
    with pytest.raises(DatasetContractError, match="衝突"):
        load_bronze_dataset(broken)
