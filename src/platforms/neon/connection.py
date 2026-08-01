"""Neon への接続。

pooled endpoint は PgBouncer の transaction pooling なので、素の psycopg 既定の
ままだと壊れる。ここで吸収する制約（docs/02_architecture.md「Neon 集約」）:

  - prepared statements を無効化する（保守的な選択・必須ではない）
    psycopg3 は同一クエリ5回目から自動で prepare する。PgBouncer 1.22 以降は
    プロトコルレベルの prepare をサポートするので現行 Neon では動くが、短命ジョブでは
    利得が無く PgBouncer 版に依存したくないため `prepare_threshold=None` で固定する。
    SQL レベルの `PREPARE` / `DEALLOCATE` は transaction mode で使えないままなので注意
    （Neon 公式ドキュメント "Connection pooling" 2026-07-31 参照）
  - クライアント側プールを重ねない（PgBouncer と二重になる）
  - 短命ジョブからの書き込みなので開いて即閉じる
  - Neon compute は suspend するため初回接続が遅い → リトライ
    （private-ops 実測: 3回・間隔 2s）

DDL / migration は pooled ではなく direct endpoint を使う。
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager

import psycopg

CONNECT_ATTEMPTS = 3
CONNECT_RETRY_INTERVAL_SECONDS = 2.0
CONNECT_TIMEOUT_SECONDS = 15

POOLED_URI_ENV = "NEON_MULTICLOUD_POOLED_URI"
DIRECT_URI_ENV = "NEON_MULTICLOUD_DIRECT_URI"


# 接続の差し替え口（DI の継ぎ目）。
#
# repository / run_sink は `connect` を **引数で受け取る**。モジュールから直接
# 呼ぶ形だと、実 DB を立てない限り1行もテストできず、計測到達点（本プロジェクトの
# 中心）が無検証のまま残る。既定値は下の `connect` なので通常利用の書き味は変わらない。
Connector = Callable[..., AbstractContextManager["psycopg.Connection"]]


class NeonConnectionError(RuntimeError):
    """接続確立に失敗した。cold start とそれ以外を切り分けられるよう原因を残す。"""


def _resolve_uri(env_name: str) -> str:
    uri = os.environ.get(env_name)
    if not uri:
        raise NeonConnectionError(
            f"{env_name} が未設定です。`doppler run -- ...` で実行してください"
        )
    return uri


@contextmanager
def connect(*, direct: bool = False) -> Iterator[psycopg.Connection]:
    """Neon へ接続する。

    direct=False: pooled endpoint（通常の読み書き）
    direct=True : direct endpoint（DDL / migration）

    suspend 明けの cold start を吸収するため初回接続を最大 CONNECT_ATTEMPTS 回試す。
    per-attempt のタイムアウトは据え置きなので、本当の障害時に待つ時間の性質は変えない。
    """
    uri = _resolve_uri(DIRECT_URI_ENV if direct else POOLED_URI_ENV)

    last_error: Exception | None = None
    for attempt in range(1, CONNECT_ATTEMPTS + 1):
        try:
            conn = psycopg.connect(
                uri,
                connect_timeout=CONNECT_TIMEOUT_SECONDS,
                # PgBouncer transaction mode では server-side prepare が使えない
                prepare_threshold=None,
            )
            break
        except psycopg.OperationalError as exc:
            last_error = exc
            if attempt < CONNECT_ATTEMPTS:
                time.sleep(CONNECT_RETRY_INTERVAL_SECONDS)
    else:
        raise NeonConnectionError(
            f"Neon への接続に {CONNECT_ATTEMPTS} 回失敗しました"
            f"（per-attempt timeout {CONNECT_TIMEOUT_SECONDS}s・compute cold start の可能性）"
        ) from last_error

    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
