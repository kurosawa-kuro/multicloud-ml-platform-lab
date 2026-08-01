"""Neon 接続の代役（psycopg の最小モック）。

**実 DB を立てずに記録層を検証する**ための継ぎ目。psycopg の
`connect() -> Connection` / `Connection.execute() -> Cursor` /
`Cursor.fetchone() / fetchall() / rowcount` / `Connection.cursor()` /
`Cursor.copy()` だけを模す。

ここで模すのは **本物が持つ振る舞いのうち、こちらが依存している分だけ**。
広く作ると「モックには通るが本物では落ちる」テストになる。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


class FakeCopy:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor

    def write_row(self, row: Any) -> None:
        self._cursor.copied_rows.append(row)

    def __enter__(self) -> FakeCopy:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


class FakeCursor:
    """1接続ぶんのカーソル。実行した SQL とパラメータを全部残す。"""

    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection
        self.copied_rows: list[Any] = []
        self.rowcount = 0
        self._result: list[Any] = []

    def execute(self, sql: str, params: Any = None) -> FakeCursor:
        self._connection.statements.append((_squeeze(sql), params))
        self._result = self._connection.next_result()
        self.rowcount = self._connection.next_rowcount()
        return self

    def fetchone(self) -> Any:
        return self._result[0] if self._result else None

    def fetchall(self) -> list[Any]:
        return list(self._result)

    def copy(self, sql: str) -> FakeCopy:
        self._connection.statements.append((_squeeze(sql), None))
        return FakeCopy(self)

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


class FakeConnection:
    def __init__(
        self,
        *,
        results: list[list[Any]] | None = None,
        rowcounts: list[int] | None = None,
    ) -> None:
        self.statements: list[tuple[str, Any]] = []
        self._results = list(results or [])
        self._rowcounts = list(rowcounts or [])
        self._cursor = FakeCursor(self)

    # psycopg の Connection.execute は暗黙にカーソルを作る
    def execute(self, sql: str, params: Any = None) -> FakeCursor:
        return self._cursor.execute(sql, params)

    def cursor(self) -> FakeCursor:
        return self._cursor

    def next_result(self) -> list[Any]:
        return self._results.pop(0) if self._results else []

    def next_rowcount(self) -> int:
        return self._rowcounts.pop(0) if self._rowcounts else 0

    @property
    def copied_rows(self) -> list[Any]:
        return self._cursor.copied_rows

    def sql_texts(self) -> list[str]:
        return [sql for sql, _ in self.statements]


class FakeConnector:
    """`Connector` の代役。`direct=` の指定も記録する（DDL は direct が要件）。"""

    def __init__(self, connection: FakeConnection | None = None) -> None:
        self.connection = connection or FakeConnection()
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)

        @contextmanager
        def _open() -> Iterator[FakeConnection]:
            yield self.connection

        return _open()


class ExplodingConnector:
    """接続そのものが失敗する場合（cold start / 権限）の代役。"""

    def __init__(self, message: str = "connection timeout") -> None:
        self.message = message

    def __call__(self, **kwargs: Any) -> Any:
        raise RuntimeError(self.message)


def _squeeze(sql: str) -> str:
    """改行と連続空白を潰して比較しやすくする。"""
    return " ".join(sql.split())
