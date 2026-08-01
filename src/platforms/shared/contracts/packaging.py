"""Tier B の配布物（wheel / stage zip）を作る前の共通チェック。

Tier B は「同一 Python パッケージ」で同一性を担保する（docs/02_architecture.md）。
そのパッケージの中には **.git も CODE_REVISION 環境変数も無い**ので、
`core.ml.config.revision` は stamp モジュール（`make stamp-revision` が生成）しか
読むものが無い。焼き忘れると:

  - ジョブが起動してから CodeRevisionError で落ちる（実行時間を捨てる）
  - 症状が「Databricks / Snowflake 側の問題」に見えて原因究明が遅れる

配布物を作る**前**にここで止める。
"""

from __future__ import annotations

from pathlib import Path

STAMP_RELATIVE_PATH = Path("src/core/ml/config/_stamp.py")


class PackagingError(RuntimeError):
    """配布物を作る前提が整っていない。"""


def require_code_revision_stamp(source_dir: Path) -> Path:
    """`_stamp.py` があり中身が入っていることを確かめる。

    Returns:
        stamp ファイルのパス（呼び出し側がログに出せるように）
    """
    stamp = Path(source_dir) / STAMP_RELATIVE_PATH
    if not stamp.exists() or "CODE_REVISION" not in stamp.read_text(encoding="utf-8"):
        raise PackagingError(
            f"{STAMP_RELATIVE_PATH} が無い（または空）。Tier B の配布物には "
            "git も環境変数も無いため、`make stamp-revision` で code_revision を"
            "焼き込んでからビルドしてください"
        )
    return stamp
