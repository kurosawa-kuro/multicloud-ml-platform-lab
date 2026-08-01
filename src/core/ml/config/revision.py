"""code_revision の解決。

`ml_runs.code_revision` は not null。**5基盤の run がこれで同一と言えなければ
比較そのものが無効**になる（docs/02_architecture.md「2階層構造」）。

解決順（上が優先）:
    1. 環境変数 CODE_REVISION
       Tier A コンテナの本番経路。docker build --build-arg で注入し ENV に落とす。
    2. git rev-parse HEAD
       ローカル開発の正。**stamp より先に見る**のは、開発機に古い stamp が
       残っていても HEAD が真実であるため（stamp はビルド時刻で固定される）。
    3. stamp モジュール（core.ml.config._stamp / `make stamp-revision` が生成）
       **Tier B の本番経路。** Databricks wheel / Snowflake stage import には
       環境変数も .git も無いため、パッケージ自身に焼き込む以外に経路が無い。
       wheel build・stage upload の直前に必ず stamp する。
    4. 解決できなければ **例外**。
       不明値（"unknown" 等）で通すと、比較不能な run が Neon に混ざり、
       contract test が「全基盤一致」を誤って緑にする。ここは落とす。
"""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path

CODE_REVISION_ENV = "CODE_REVISION"


class CodeRevisionError(RuntimeError):
    """code_revision を解決できない。この状態の run は記録してはいけない。"""


def _from_env() -> str | None:
    value = (os.getenv(CODE_REVISION_ENV) or "").strip()
    return value or None


def _from_stamp() -> str | None:
    """ビルド時に生成される core.ml.config._stamp を読む（無ければ None）。

    from-import だと親パッケージの属性キャッシュが sys.modules より先に
    解決されるため、importlib で毎回モジュール解決する。
    """
    import importlib

    try:
        stamp = importlib.import_module("core.ml.config._stamp")
    except ImportError:
        return None
    value = (getattr(stamp, "CODE_REVISION", "") or "").strip()
    return value or None


def _from_git() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[4],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


@lru_cache(maxsize=1)
def code_revision() -> str:
    """git SHA を返す。解決できなければ CodeRevisionError。

    学習を3分走らせてから落ちると試行時間が無駄になるため、
    呼び出し側（pipeline / track）は**処理の先頭**でこれを解決すること。
    """
    value = _from_env() or _from_git() or _from_stamp()
    if not value:
        raise CodeRevisionError(
            f"code_revision を解決できません。コンテナは {CODE_REVISION_ENV} を"
            f"ビルド時に注入、wheel / stage は `make stamp-revision` で焼き込んでください"
        )
    return value
