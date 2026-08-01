"""california_housing の INSERT / SELECT。

接続は **引数で受け取る**（`connect: Connector = default_connect`）。
モジュール内で直接 `connect()` を呼ぶと実 DB 無しでは1行も検証できず、
計測到達点が無検証のまま残る（tests/test_neon_repository.py が偽接続を差し込む）。

20640 行を executemany で入れると往復が効いてくるので COPY を使う。
再ロードを冪等にするため row_id 衝突は無視する（on conflict do nothing）。
COPY 自体は on conflict を持たないので、一時テーブル経由で流し込む。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from platforms.neon.connection import Connector
from platforms.neon.connection import connect as default_connect
from platforms.neon.records import INSERT_COLUMNS, HousingRecord
from platforms.neon.schema import TABLE_NAME

_COLS = ", ".join(INSERT_COLUMNS)


def insert_many(
    records: Sequence[HousingRecord],
    *,
    batch_label: str = "",
    connect: Connector = default_connect,
) -> int:
    """COPY で一括投入する。既存 row_id はスキップし、実際に入った行数を返す。"""
    if not records:
        return 0

    staging = f"{TABLE_NAME}_staging"
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"create temp table {staging} (like {TABLE_NAME} including defaults) on commit drop"
            )
            with cur.copy(f"copy {staging} ({_COLS}) from stdin") as copy:
                for record in records:
                    copy.write_row(record.as_row())

            cur.execute(
                f"insert into {TABLE_NAME} ({_COLS}) "
                f"select {_COLS} from {staging} "
                f"on conflict (row_id) do nothing"
            )
            return cur.rowcount


def count(*, connect: Connector = default_connect) -> int:
    """行数。ロード結果の検証に使う。"""
    with connect() as conn:
        row = conn.execute(f"select count(*) from {TABLE_NAME}").fetchone()
        if row is None:  # pragma: no cover - count(*) は必ず1行返る
            raise RuntimeError("count(*) が行を返さなかった")
        return int(row[0])


def fetch_all(
    limit: int | None = None,
    offset: int = 0,
    *,
    connect: Connector = default_connect,
) -> list[HousingRecord]:
    """row_id 順に読む。順序を固定しないと分割の再現性が壊れる。"""
    sql = f"select {_COLS} from {TABLE_NAME} order by row_id"
    params: list[object] = []
    if limit is not None:
        sql += " limit %s"
        params.append(limit)
    if offset:
        sql += " offset %s"
        params.append(offset)

    with connect() as conn:
        rows = conn.execute(sql, params or None).fetchall()
        return [HousingRecord.from_row(row) for row in rows]


def fetch_by_ids(
    row_ids: Iterable[int], *, connect: Connector = default_connect
) -> list[HousingRecord]:
    """指定 row_id だけ読む。1件推論の payload 生成に使う。"""
    ids = list(row_ids)
    if not ids:
        return []
    with connect() as conn:
        rows = conn.execute(
            f"select {_COLS} from {TABLE_NAME} where row_id = any(%s) order by row_id",
            (ids,),
        ).fetchall()
        return [HousingRecord.from_row(row) for row in rows]


def fetch_dataframe(*, connect: Connector = default_connect):
    """配布用に全件を pandas DataFrame で取り出す。

    列名は core.ml.config.constants の定義（snake_case）と一致している。
    pandas はこの関数の中でだけ import する（接続層を ML 依存で汚さない）。
    """
    import pandas as pd

    with connect() as conn:
        rows = conn.execute(f"select {_COLS} from {TABLE_NAME} order by row_id").fetchall()
    return pd.DataFrame(rows, columns=list(INSERT_COLUMNS))


def summary(*, connect: Connector = default_connect) -> dict[str, float]:
    """目的変数の要約。基盤間でデータが同一であることの粗い照合に使う。"""
    with connect() as conn:
        row = conn.execute(
            f"select count(*), avg(med_house_val), min(med_house_val), "
            f"max(med_house_val), stddev_pop(med_house_val) from {TABLE_NAME}"
        ).fetchone()
    if row is None:  # pragma: no cover - 集約は必ず1行返る
        raise RuntimeError("集約クエリが行を返さなかった")
    return {
        "rows": int(row[0]),
        "mean": float(row[1]),
        "min": float(row[2]),
        "max": float(row[3]),
        "std": float(row[4]),
    }
