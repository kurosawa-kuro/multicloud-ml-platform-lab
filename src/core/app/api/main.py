"""Tier A 推論アプリの起動。

流用元: private-ops starter-kit `api-fastapi/src/micropost_api/main.py`。
**変更点**: init_db() を削除した（モデルは predictor 側で遅延ロード）。
create_app() ファクトリと構造化ログ middleware はそのまま踏襲している。

1イメージで Vertex / SageMaker / Azure ML の3契約を満たす。
SageMaker は port 8080 固定なので、起動時のポート指定を間違えない。
"""

from fastapi import FastAPI

from core.app.api.routes import router
from core.app.observability.logging_setup import RequestLoggingMiddleware, configure_logging


def create_app() -> FastAPI:
    logger = configure_logging("multicloud-ml-serving")
    api = FastAPI(title="Multicloud ML Serving (Tier A: 1 image / 3 contracts)")
    api.add_middleware(RequestLoggingMiddleware, logger=logger)
    api.include_router(router)
    return api


app = create_app()
