"""contract test: 全基盤の run が同一の学習コードで動いたことの検証。

Tier A は同一コンテナ、Tier B は同一 wheel / stage パッケージで同一性を担保するが、
形が違う以上「同じコードが動いた」と言えるのは記録から機械的に示せるときだけ。
ここが崩れると比較そのものが無効になる。

## 「同一SHA」ではなく「同一の学習コード」を見る理由（2026-08-01 実測）

5基盤の `ml_runs.code_revision` は**5つとも別のコミット**だった:

    vertex     35d48cb    sagemaker  7e6dc1c    databricks 4129907
    azureml    ddb1f09    snowflake  e4eeaab

5基盤を1日で順に回した結果、その間に adapter / docs のコミットが挟まったため。
しかし **`src/core/ml` の tree hash は5つとも `a1b73934` で完全一致**しており、
学習ロジックはバイト単位で同一だった。

したがって比較の前提として検証すべきなのは commit SHA の一致ではなく
**学習サブツリーの一致**。SHA を条件にすると、意味のない差で落ちるか、
逆に「SHA を揃えたが core/ml は変わっていた」を見逃す。

このテストが落ちた状態で出したメトリクス差は、基盤の差ではなくコードの差。
"""

from __future__ import annotations

import re

from tests.platform_runs import (
    TRAINING_SUBTREE,
    commit_exists,
    git_tree_hash,
    load_runs,
)

SHA1_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def test_all_platforms_share_one_training_tree() -> None:
    """全基盤の run が同一の `src/core/ml` ツリーで動いたこと。

    比較が成立するための中核の前提。commit SHA は違ってよい（上の docstring）。
    """
    trees = {run.platform: git_tree_hash(run.code_revision) for run in load_runs()}

    assert len(set(trees.values())) == 1, (
        f"{TRAINING_SUBTREE} が基盤間で異なる = 別のコードで比較している: {trees}"
    )


def test_code_revision_is_not_null() -> None:
    """code_revision が空の run が無いこと。

    コンテナ / wheel の中に .git は無いため、ビルド時に埋め込む。
    埋め込み漏れは空文字で通ってしまうので、not null だけでなく形も見る。
    """
    for run in load_runs():
        assert run.code_revision, f"{run.platform} の code_revision が空"
        assert SHA1_PATTERN.match(run.code_revision), (
            f"{run.platform} の code_revision が 40 桁 SHA-1 でない: {run.code_revision!r}"
        )


def test_code_revision_exists_in_this_repository() -> None:
    """記録された SHA が実在するコミットであること（捏造・古い値の検出）。"""
    for run in load_runs():
        assert commit_exists(run.code_revision), (
            f"{run.platform} の code_revision がこのリポジトリに存在しない: {run.code_revision}"
        )
