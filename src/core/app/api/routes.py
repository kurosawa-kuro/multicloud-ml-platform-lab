"""Tier A 推論アプリのルーティング。

流用元: private-ops starter-kit `api-fastapi/src/micropost_api/web/routes.py`。
**変更点**: DB CRUD を推論3契約に差し替えた。APIRouter 構成・healthz・
DI（Depends）の形はそのまま踏襲している。

1つのアプリで3基盤の推論契約を同時に満たす（docs/02_architecture.md）:

    /health + /predict      Vertex   ({"instances": [...]} -> {"predictions": [...]})
    /ping   + /invocations  SageMaker (port 8080)
    /score                  Azure ML managed online endpoint

**境界でリクエスト形式だけ変換し、中心の predict() は1つに保つ。**
契約ごとに推論処理を分けると、3基盤で別の結果が出ても気付けない。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from core.app.serving.predictor import Predictor, get_predictor

router = APIRouter()


class PredictRequest(BaseModel):
    """Vertex / SageMaker / Azure ML 共通の入力形。"""

    instances: list[dict[str, Any]] = Field(..., min_length=1)


class PredictResponse(BaseModel):
    predictions: list[float]


def _predict(payload: PredictRequest, predictor: Predictor) -> PredictResponse:
    """3契約が共有する唯一の推論経路。ここを契約ごとに分岐させない。"""
    try:
        return PredictResponse(predictions=predictor.predict(payload.instances))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


# --- Vertex AI 契約 -------------------------------------------------------


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/predict", response_model=PredictResponse)
def vertex_predict(
    payload: PredictRequest, predictor: Annotated[Predictor, Depends(get_predictor)]
) -> PredictResponse:
    return _predict(payload, predictor)


# --- SageMaker 契約（port 8080）-------------------------------------------


@router.get("/ping")
def ping() -> dict[str, str]:
    """SageMaker のヘルスチェック。200 を返すことだけが要件。"""
    return {"status": "ok"}


@router.post("/invocations", response_model=PredictResponse)
def invocations(
    payload: PredictRequest, predictor: Annotated[Predictor, Depends(get_predictor)]
) -> PredictResponse:
    return _predict(payload, predictor)


# --- Azure ML 契約 --------------------------------------------------------


@router.post("/score", response_model=PredictResponse)
def score(
    payload: PredictRequest, predictor: Annotated[Predictor, Depends(get_predictor)]
) -> PredictResponse:
    return _predict(payload, predictor)
