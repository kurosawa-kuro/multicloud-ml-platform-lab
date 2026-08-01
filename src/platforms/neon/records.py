"""California Housing の1レコード。

sklearn.datasets.fetch_california_housing の列を snake_case に落としたもの。
Kaggle / handson-ml 版（OCEAN_PROXIMITY 等・One-Hot あり）は列も目的変数スケールも
別物なので混入させない（Snowflake の事前調査で確認済み）。
"""

from __future__ import annotations

from dataclasses import astuple, dataclass

DATASET_SOURCE = "sklearn.datasets.fetch_california_housing"

# sklearn の列名 -> テーブル列名。順序が COPY の列順の正本。
COLUMN_MAP: dict[str, str] = {
    "MedInc": "med_inc",
    "HouseAge": "house_age",
    "AveRooms": "ave_rooms",
    "AveBedrms": "ave_bedrms",
    "Population": "population",
    "AveOccup": "ave_occup",
    "Latitude": "latitude",
    "Longitude": "longitude",
}

FEATURE_COLUMNS: tuple[str, ...] = tuple(COLUMN_MAP.values())
TARGET_COLUMN = "med_house_val"

INSERT_COLUMNS: tuple[str, ...] = ("row_id", *FEATURE_COLUMNS, TARGET_COLUMN)


@dataclass(frozen=True, slots=True)
class HousingRecord:
    """1行。row_id は sklearn の元配列の添字。

    row_id を保持するのは、seed 固定の分割を後から再現・照合できるようにするため。
    採番を DB 側 serial に任せると、取得順が変わったときに黙って対応がずれる。
    """

    row_id: int
    med_inc: float
    house_age: float
    ave_rooms: float
    ave_bedrms: float
    population: float
    ave_occup: float
    latitude: float
    longitude: float
    med_house_val: float

    def as_row(self) -> tuple:
        """INSERT_COLUMNS の順に並んだタプル。COPY / executemany へそのまま渡せる。"""
        return astuple(self)

    @classmethod
    def from_row(cls, row: tuple) -> HousingRecord:
        """SELECT INSERT_COLUMNS の結果行から復元する。"""
        return cls(int(row[0]), *(float(v) for v in row[1:]))
