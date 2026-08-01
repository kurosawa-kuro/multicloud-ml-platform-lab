"""Tier A 推論アプリの3契約テスト。

流用元: private-ops starter-kit `api-fastapi/tests/conftest.py` の
TestClient + `app.dependency_overrides` の形をそのまま踏襲している
（DB セッション差し替えの代わりに Predictor を差し替える）。

**3契約が同一の予測値を返すこと**が本テストの主眼。ここが崩れると、
「1イメージで3基盤」という Tier A の前提が成立していないことになる。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from core.app.api.main import app
from core.app.serving.predictor import Predictor, get_predictor
from core.ml.config.constants import FEATURE_COLUMNS

PREDICT_CONTRACTS = ["/predict", "/invocations", "/score"]
HEALTH_CONTRACTS = ["/health", "/ping"]


class StubPredictor(Predictor):
    """モデルファイルを持たずに契約だけ検証する。

    実モデルの読み込みは Phase 1 の smoke（実イメージ）で見る。
    ここで実ファイルを要求すると、契約テストがモデル生成に依存して壊れやすくなる。
    """

    def __init__(self) -> None:
        self._feature_names = list(FEATURE_COLUMNS)

    def predict(self, instances: list[dict[str, Any]]) -> list[float]:
        missing = [c for c in self._feature_names if c not in (instances[0] or {})]
        if missing:
            raise ValueError(f"特徴量が不足しています: {missing}")
        # 列順に依存した値を返す（並びが崩れたら結果が変わることをテストで検出できる）
        return [
            float(sum(row[c] * i for i, c in enumerate(self._feature_names))) for row in instances
        ]


@pytest.fixture
def client() -> Iterator[TestClient]:
    app.dependency_overrides[get_predictor] = StubPredictor
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def instance() -> dict[str, float]:
    return {name: float(i + 1) for i, name in enumerate(FEATURE_COLUMNS)}


@pytest.mark.parametrize("path", HEALTH_CONTRACTS)
def test_health_contracts_return_200(client: TestClient, path: str) -> None:
    """Vertex は /health、SageMaker は /ping を叩く。両方 200 であること。"""
    assert client.get(path).status_code == 200


@pytest.mark.parametrize("path", PREDICT_CONTRACTS)
def test_predict_contracts_return_predictions(
    client: TestClient, instance: dict[str, float], path: str
) -> None:
    res = client.post(path, json={"instances": [instance]})
    assert res.status_code == 200, res.text
    assert len(res.json()["predictions"]) == 1


def test_all_three_contracts_agree(client: TestClient, instance: dict[str, float]) -> None:
    """3契約が同一の予測値を返すこと。

    契約ごとに推論処理が分岐していると、3基盤で別の結果が出ても気付けない。
    """
    results = [
        client.post(path, json={"instances": [instance]}).json()["predictions"]
        for path in PREDICT_CONTRACTS
    ]
    assert results[0] == results[1] == results[2]


@pytest.mark.parametrize("path", PREDICT_CONTRACTS)
def test_missing_feature_returns_400(
    client: TestClient, instance: dict[str, float], path: str
) -> None:
    broken = {k: v for k, v in instance.items() if k != FEATURE_COLUMNS[0]}
    res = client.post(path, json={"instances": [broken]})
    assert res.status_code == 400
    assert FEATURE_COLUMNS[0] in res.json()["detail"]


@pytest.mark.parametrize("path", PREDICT_CONTRACTS)
def test_empty_instances_returns_422(client: TestClient, path: str) -> None:
    assert client.post(path, json={"instances": []}).status_code == 422


def test_predictor_orders_columns_by_training_order(instance: dict[str, float]) -> None:
    """入力 dict のキー順に結果が依存しないこと。

    3基盤のクライアントごとにキー順が変わっても同じ予測値でなければならない。
    """
    predictor = StubPredictor()
    reversed_keys = dict(reversed(list(instance.items())))
    assert predictor.predict([instance]) == predictor.predict([reversed_keys])
