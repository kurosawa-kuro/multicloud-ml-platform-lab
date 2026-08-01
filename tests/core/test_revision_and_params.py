"""code_revision 解決と params 型復元の契約。

どちらも「基盤側の環境差で core が落ちる / 黙って狂う」の芽。
Tier B（wheel / stage）には env も .git も無いため stamp 経路が生命線。
"""

from __future__ import annotations

import sys
import types

import pytest

from core.ml.config import revision as revision_mod
from core.ml.config.params import coerce_param_types, split_runner_params
from core.ml.config.revision import CODE_REVISION_ENV, code_revision


@pytest.fixture(autouse=True)
def _clear_cache():
    code_revision.cache_clear()
    yield
    code_revision.cache_clear()
    sys.modules.pop("core.ml.config._stamp", None)


def test_env_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CODE_REVISION_ENV, "e" * 40)
    assert code_revision() == "e" * 40


def test_stamp_is_used_when_env_and_git_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tier B の本番経路: env も git も無い環境で stamp を読む。"""
    monkeypatch.delenv(CODE_REVISION_ENV, raising=False)
    monkeypatch.setattr(revision_mod, "_from_git", lambda: None)
    stamp = types.ModuleType("core.ml.config._stamp")
    stamp.CODE_REVISION = "f" * 40
    monkeypatch.setitem(sys.modules, "core.ml.config._stamp", stamp)
    assert code_revision() == "f" * 40


def test_git_wins_over_stale_stamp(monkeypatch: pytest.MonkeyPatch) -> None:
    """開発機に古い stamp が残っていても HEAD が勝つこと。"""
    monkeypatch.delenv(CODE_REVISION_ENV, raising=False)
    monkeypatch.setattr(revision_mod, "_from_git", lambda: "9" * 40)
    stamp = types.ModuleType("core.ml.config._stamp")
    stamp.CODE_REVISION = "f" * 40
    monkeypatch.setitem(sys.modules, "core.ml.config._stamp", stamp)
    assert code_revision() == "9" * 40


def test_unresolvable_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """不明値で通さない。比較不能な run が Neon に混ざる方が高くつく。"""
    monkeypatch.delenv(CODE_REVISION_ENV, raising=False)
    monkeypatch.setattr(revision_mod, "_from_stamp", lambda: None)
    monkeypatch.setattr(revision_mod, "_from_git", lambda: None)
    with pytest.raises(revision_mod.CodeRevisionError):
        code_revision()


# --- params（SageMaker は全値を文字列で渡す）------------------------------


def test_coerce_restores_json_literals() -> None:
    coerced = coerce_param_types(
        {"learning_rate": "0.05", "num_boost_round": "500", "deterministic": "true", "note": "abc"}
    )
    assert coerced == {
        "learning_rate": 0.05,
        "num_boost_round": 500,
        "deterministic": True,
        "note": "abc",
    }


def test_coerce_keeps_native_types() -> None:
    """Tier B は native 型で来る。二重変換しないこと。"""
    params = {"learning_rate": 0.05, "num_boost_round": 500}
    assert coerce_param_types(params) == params


def test_split_runner_params() -> None:
    runner, overrides = split_runner_params(
        {"seed": 42, "num_boost_round": 500, "learning_rate": 0.1}
    )
    assert runner == {"seed": 42, "num_boost_round": 500}
    assert overrides == {"learning_rate": 0.1}
