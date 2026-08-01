"""Bronze layer.

流用元: private-ops starter-kit `housing_ml/data/load.py`。
**変更点**: sklearn 直取得を DataFrame 受け取りに置き換えた。

理由: 5基盤で入力の作り方が異なる（Vertex/SageMaker/AzureML は Parquet、
Databricks は Volume/Spark、Snowflake はテーブル直読み）。sklearn を
ここで呼ぶと Snowflake / Databricks で「データのある場所で計算」できない。
配布の起点は Neon（docs/tasks/03_active/2026-07-31-core-ml-5基盤対応実装.md）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from core.ml.config.constants import DATASET_NAME, FEATURE_COLUMNS, TARGET_COLUMN


class DatasetContractError(ValueError):
    """入力データが契約を満たさない。

    5基盤で同じデータを使っていることの機械的な担保。ここを緩めると
    metric parity test が意味を失う。学習が進んでから落ちると失敗原因が
    data なのか iam なのか切り分けられなくなるので fail-fast にする。
    """


@dataclass(frozen=True)
class RawDataset:
    """Bronze layer: source data as received from the platform."""

    features: pd.DataFrame
    target: pd.Series
    feature_names: list[str]
    dataset_name: str = DATASET_NAME


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """列名を契約（snake_case 小文字）に揃える。

    **Snowflake は識別子を大文字に畳む**ため、`session.table(...).to_pandas()` は
    MED_INC のような列名で返る。ここで揃えないと Tier B だけ列欠落で落ちる。
    Databricks / Spark も大文字混じりになりうるので、入口で1度だけ正規化する。

    正規化は「前後空白の除去」と「小文字化」のみ。区切り文字の変換（キャメル →
    スネーク等）はしない。暗黙の変換を増やすと、別データセットに差し替えたとき
    黙って違う列に当たる。
    """
    renamed = {c: str(c).strip().lower() for c in df.columns}
    lowered = list(renamed.values())
    duplicated = sorted({c for c in lowered if lowered.count(c) > 1})
    if duplicated:
        # MED_INC と med_inc が併存している等。黙って rename すると片方が
        # もう片方を上書きし、どちらのデータで学習したか分からなくなる。
        raise DatasetContractError(f"正規化後に列名が衝突します: {duplicated}")
    if renamed == {c: c for c in df.columns}:
        return df
    return df.rename(columns=renamed)


def load_bronze_dataset(df: pd.DataFrame) -> RawDataset:
    """各基盤が作った DataFrame を Bronze 層に受ける。

    列名を正規化し、row_id 順に並べ替えてから列を切り出す。
    行順を入力任せにすると、基盤ごとの DataFrame の作り方の違いで分割がずれ、
    metric parity が落ちる。
    """
    df = normalize_columns(df)
    if "row_id" not in df.columns:
        # row_id 無しを許すと行順が基盤依存になり、同じ seed でも分割がずれて
        # metric parity が**黙って**壊れる。欠けは設定ミスなので即落とす。
        raise DatasetContractError("row_id 列が必要です（決定的な行順の担保）")
    if df["row_id"].duplicated().any():
        raise DatasetContractError("row_id が重複しています")
    ordered = df.sort_values("row_id")
    ordered = ordered.reset_index(drop=True)
    available = [c for c in FEATURE_COLUMNS if c in ordered.columns]
    return RawDataset(
        features=ordered[available],
        target=ordered[TARGET_COLUMN]
        if TARGET_COLUMN in ordered.columns
        else pd.Series(dtype=float),
        feature_names=list(available),
    )


def read_parquet(path: Path) -> pd.DataFrame:
    """Tier A 向け。ディレクトリを渡された場合は中の Parquet 1本を読む。

    SageMaker は `/opt/ml/input/data/<channel>` にディレクトリで渡すため、
    ファイル / ディレクトリの両方を受ける。
    """
    if path.is_dir():
        candidates = sorted(path.glob("*.parquet"))
        if not candidates:
            raise FileNotFoundError(f"Parquet が見つかりません: {path}")
        if len(candidates) > 1:
            raise ValueError(
                f"Parquet が複数あります（配布物は単一ファイルであること）: "
                f"{[p.name for p in candidates]}"
            )
        path = candidates[0]
    return pd.read_parquet(path)


def to_parquet(df: pd.DataFrame, path: Path) -> Path:
    """配布用の単一 Parquet を書き出す。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return path


def checksum(path: Path) -> str:
    """配布物の同一性を担保する。

    code_revision がコードの同一性を担保するのと同じ役割をデータで果たす。
    """
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
