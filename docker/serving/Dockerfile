# Tier A（Vertex AI / SageMaker AI / Azure ML）共通の**推論**イメージ。
#
# 学習イメージ（docker/training）と分けるのは、推論側に要るものが違うから:
#   要る   : fastapi / uvicorn（3契約のルート）
#   要らない: scikit-learn（分割・評価は学習時のみ）、psycopg（推論は ml_runs を書かない）
#
# 3基盤で **同一イメージ**（Tier A の同一性担保）。契約の差は:
#   ルート    -> core/app/api/routes.py（/predict, /invocations, /score）
#   モデル所在 -> docker/serving/entrypoint.sh
#
# lightgbm / pandas は学習イメージと**同一 minor**（.claude/rules/python.md）。
# ずれると同じ model.txt でも予測値が変わり、metric parity が無意味になる。
#
# ビルド:
#   docker build -f docker/serving/Dockerfile \
#     --build-arg CODE_REVISION="$(git rev-parse HEAD)" -t <registry>/serving:<tag> .

FROM python:3.12-slim

# libgomp1 = LightGBM の OpenMP ランタイム。これが無いと import 時点で落ちる。
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONPATH=/app/src \
    PORT=8080

WORKDIR /app

# バージョンは pyproject.toml と揃える（学習と serving で同一 minor）。
RUN uv pip install --system --no-cache \
        "lightgbm>=4.6,<4.7" \
        "pandas>=2.3,<2.4" \
        "fastapi>=0.120,<0.121" \
        "uvicorn>=0.38,<0.39"

# 推論に要るのは core/app と、そこから使う core/ml/config（列名の契約）だけ。
# platforms/ は入れない（SDK 依存を推論コンテナに持ち込まない）。
COPY src/core /app/src/core

COPY docker/serving/entrypoint.sh docker/serving/fetch_gcs_model.py /app/
RUN chmod +x /app/entrypoint.sh

# 学習側と同じく、実際に動いたコードの SHA を焼き込む（コンテナ内に .git は無い）。
ARG CODE_REVISION
ENV CODE_REVISION=${CODE_REVISION}

RUN useradd --create-home --uid 10001 mlrunner \
    && chown -R mlrunner:mlrunner /app
USER mlrunner

EXPOSE 8080

# SageMaker は `serve` 引数付きで起動する（シムが読み飛ばす）。
# Vertex / Azure ML は引数なし。3基盤とも同じ ENTRYPOINT で足りる。
ENTRYPOINT ["/app/entrypoint.sh"]
