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

## 2026-08-02: 履歴リセットで上の5コミットが解決不能になった

公開のため git 履歴を作り直した（`rm -rf .git && git init`）。上の5 SHA は旧履歴にあり、
このリポジトリからは二度と解決できない。

**失われたのは再検証の手段であって、検証した事実ではない**——tree hash が5つとも
`a1b73934` で一致していたことはリセット前に実測済みで、その記録がこの docstring と
`docs/comparison/` に残る。ただし値を再導出できない以上、定数と突き合わせる形の
テストは同語反復にしかならないので書かない。代わりに免除を5 SHA の名指しに閉じ込め、
**それ以外の未解決 SHA は落とす**（`test_only_pre_reset_revisions_are_unresolvable`）。
"""

from __future__ import annotations

import re

from tests.platform_runs import (
    PRE_HISTORY_RESET_REVISIONS,
    TRAINING_SUBTREE,
    commit_exists,
    git_tree_hash,
    load_runs,
)

SHA1_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def test_all_platforms_share_one_training_tree() -> None:
    """解決できる run が同一の `src/core/ml` ツリーで動いたこと。

    比較が成立するための中核の前提。commit SHA は違ってよい（上の docstring）。

    2026-08-02 の履歴リセットで 5 基盤の run は解決不能になった（`platform_runs.py` の
    `PRE_HISTORY_RESET_REVISIONS`）。**その5件を定数と突き合わせても同語反復**なので、
    ここでは検証せず、下の `test_...only_pre_reset_revisions_are_unresolvable` が
    「未解決なのはあの5件だけ」を守る。以後の run はこのテストが本来の強さで見る。
    """
    trees = {
        run.platform: git_tree_hash(run.code_revision)
        for run in load_runs()
        if commit_exists(run.code_revision)
    }
    if not trees:
        return  # 解決できる run がまだ無い（履歴リセット直後）

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


def test_only_pre_reset_revisions_are_unresolvable() -> None:
    """記録された SHA が実在すること。例外は履歴リセット前の5件**だけ**。

    元は「全 run のコミットが実在する」だった（捏造・古い値の検出）。2026-08-02 の
    履歴リセットで5件が解決不能になったが、**「解決できないものは飛ばす」に緩めない**。
    それをやると、埋め込み漏れや別リポ由来の SHA を持つ新しい run が黙って通る。
    免除は名指しした5件に限り、それ以外の未解決 SHA は落とす。
    """
    unresolved = {
        run.platform: run.code_revision
        for run in load_runs()
        if not commit_exists(run.code_revision)
    }
    unexpected = {p: sha for p, sha in unresolved.items() if sha not in PRE_HISTORY_RESET_REVISIONS}

    assert not unexpected, (
        "履歴リセット前の5件以外に、このリポジトリで解決できない code_revision がある"
        f"（埋め込み漏れか別リポ由来を疑う）: {unexpected}"
    )
