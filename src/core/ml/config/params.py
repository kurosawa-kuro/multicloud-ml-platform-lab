"""ハイパーパラメータの型復元と振り分け。

**SageMaker は hyperparameters.json の全値を文字列で渡してくる**
（"0.05" であって 0.05 ではない）。ここで型を戻さないと LightGBM に
文字列が渡り、基盤によって挙動が変わる。Tier B は native 型で渡してくるが、
同じ関数を通しても無害なので、5基盤とも同じ経路で正規化する。

変換は JSON リテラルとして解釈できるものだけ（int / float / true / false / null）。
それ以外は文字列のまま残す。曖昧な推測変換を増やすと、基盤側の渡し方の差が
ここで黙って吸収されてしまい、契約差そのものを観測できなくなる。
"""

from __future__ import annotations

import json
from typing import Any

# 学習ランナー自身が消費するキー。LightGBM へは渡さない。
RUNNER_KEYS: frozenset[str] = frozenset({"seed", "num_boost_round", "early_stopping_rounds"})


def coerce_param_types(params: dict[str, Any]) -> dict[str, Any]:
    """文字列で渡された数値・真偽値を JSON リテラルとして復元する。"""
    coerced: dict[str, Any] = {}
    for key, value in params.items():
        if isinstance(value, str):
            try:
                coerced[key] = json.loads(value)
            except json.JSONDecodeError:
                coerced[key] = value
        else:
            coerced[key] = value
    return coerced


def split_runner_params(params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(ランナー用, LightGBM override 用) に分ける。

    LightGBM 側は default_lightgbm_params の**後勝ち**でマージされる。
    既定と違う値で走らせた事実は ml_runs.params に残るため、
    「差が出たのはパラメータのせいか」を後から照合できる。
    """
    runner = {k: v for k, v in params.items() if k in RUNNER_KEYS}
    overrides = {k: v for k, v in params.items() if k not in RUNNER_KEYS}
    return runner, overrides
