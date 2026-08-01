"""再開値の解決（platforms/shared/resume.py）の契約。

`resume` を引数なしで動かすための層。ここで固定するのは:

  1. 直近の成功 run から値を引く（train → 成果物 URI / register → モデル参照）
  2. **学習コードが違う run を掴まない**（tree hash 不一致は拒否）
  3. 判定できないときは「一致した」に倒さず拒否する
  4. 無いときは何を渡せばよいかを文言に含める

実 DB は叩かない（connect を注入する）。
"""

from __future__ import annotations

from typing import Any

import pytest

from core.telemetry.schemas import Platform
from platforms.shared import resume as resume_module
from platforms.shared.resume import (
    ResumeError,
    latest_artifact_uri,
    latest_model_reference,
)

ARTIFACT = "azureml://jobs/purple_chain/outputs/model"


class FakeConnection:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self._row = row
        self.queries: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: tuple[Any, ...]) -> FakeConnection:
        self.queries.append((sql, params))
        return self

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def fake_connect(row: tuple[Any, ...] | None):
    return lambda: FakeConnection(row)


@pytest.fixture
def same_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    """記録された revision と HEAD の学習サブツリーが一致する状態。"""
    monkeypatch.setattr(resume_module, "training_tree", lambda revision="HEAD": "tree-aaa")


def test_artifact_uri_comes_from_the_latest_successful_train(same_tree: None) -> None:
    point = latest_artifact_uri(
        Platform.AZUREML,
        connect=fake_connect(("run-1", "abc123", {"model_artifact_uri": ARTIFACT})),
    )

    assert (point.run_id, point.value) == ("run-1", ARTIFACT)


def test_query_filters_to_rows_that_actually_carry_the_key(same_tree: None) -> None:
    """`params ? key` で絞る。キーの無い行を掴むと None を返して後段が謎に落ちる。"""
    connection = FakeConnection(("run-1", "abc123", {"model_artifact_uri": ARTIFACT}))

    latest_artifact_uri(Platform.AZUREML, connect=lambda: connection)

    sql, params = connection.queries[0]
    assert "status = 'success'" in sql
    assert "order by created_at desc" in sql
    assert params == ("azureml", "train", "model_artifact_uri")


def test_different_training_code_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """**別の学習コードで作られた成果物を掴まない**（比較が静かに無効になる）。"""
    monkeypatch.setattr(
        resume_module,
        "training_tree",
        lambda revision="HEAD": "tree-new" if revision == "HEAD" else "tree-old",
    )

    with pytest.raises(ResumeError, match="別の学習コードで作られている"):
        latest_artifact_uri(
            Platform.AZUREML,
            connect=fake_connect(("run-1", "abc123", {"model_artifact_uri": ARTIFACT})),
        )


def test_unresolvable_revision_is_refused_not_assumed(monkeypatch: pytest.MonkeyPatch) -> None:
    """判定できないときに「一致した」へ倒さない（消えた commit の成果物を掴まない）。"""
    monkeypatch.setattr(
        resume_module,
        "training_tree",
        lambda revision="HEAD": "tree-aaa" if revision == "HEAD" else None,
    )

    with pytest.raises(ResumeError, match="判定できない"):
        latest_artifact_uri(
            Platform.AZUREML,
            connect=fake_connect(("run-1", "gone", {"model_artifact_uri": ARTIFACT})),
        )


def test_absence_says_what_to_pass() -> None:
    with pytest.raises(ResumeError, match="--artifact-uri"):
        latest_artifact_uri(Platform.VERTEX, connect=fake_connect(None))


def test_model_reference_accepts_the_key_each_platform_uses(same_tree: None) -> None:
    """参照キーは5基盤で違う（リソース名 / 版 / ARN）。最初に見つかったものを返す。"""
    point = latest_model_reference(
        Platform.DATABRICKS, connect=fake_connect(("run-9", "abc123", {"model_version": "3"}))
    )

    assert point.value == "3"


def test_training_tree_returns_none_for_unknown_revision() -> None:
    """存在しない revision は None（例外にしない。判定不能として扱う）。"""
    assert resume_module.training_tree("0000000000000000000000000000000000000000") is None


def test_training_tree_resolves_head() -> None:
    """空振りテストにしない（実リポジトリで解決できることを確認）。"""
    assert resume_module.training_tree("HEAD")


def test_merge_run_params_never_inserts() -> None:
    """params 追記が INSERT に化けないこと。

    化けると、ジョブが Neon へ届かなかったときに adapter が行を作り
    `write_path='direct'` を騙る（オーケストレータの egress をジョブの egress と偽る）。
    """
    from platforms.neon.run_sink import _MERGE_PARAMS_SQL

    assert _MERGE_PARAMS_SQL.strip().lower().startswith("update ml_runs")
    assert "insert" not in _MERGE_PARAMS_SQL.lower()
    assert "where run_id" in _MERGE_PARAMS_SQL
