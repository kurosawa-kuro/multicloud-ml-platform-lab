"""california_housing テーブルの DDL。

DDL は pooled ではなく **direct endpoint** で流す。
このモジュールが本テーブルの正本（計測3テーブルの正本は sql/schema.sql）。
"""

from __future__ import annotations

from platforms.neon.connection import Connector
from platforms.neon.connection import connect as default_connect

TABLE_NAME = "california_housing"

CREATE_TABLE_SQL = f"""
create table if not exists {TABLE_NAME} (
    row_id        integer primary key,
    med_inc       double precision not null,
    house_age     double precision not null,
    ave_rooms     double precision not null,
    ave_bedrms    double precision not null,
    population    double precision not null,
    ave_occup     double precision not null,
    latitude      double precision not null,
    longitude     double precision not null,
    med_house_val double precision not null,
    source        text        not null default 'sklearn.datasets.fetch_california_housing',
    ingested_at   timestamptz not null default now()
)
"""

DROP_TABLE_SQL = f"drop table if exists {TABLE_NAME}"


def create_table(*, connect: Connector = default_connect) -> None:
    """テーブルを作る（冪等）。"""
    with connect(direct=True) as conn:
        conn.execute(CREATE_TABLE_SQL)


def drop_table(*, connect: Connector = default_connect) -> None:
    """テーブルを消す。ロードのやり直し用。"""
    with connect(direct=True) as conn:
        conn.execute(DROP_TABLE_SQL)
