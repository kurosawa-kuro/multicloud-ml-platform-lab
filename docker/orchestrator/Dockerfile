# Vertex AI Pipelines のステップ用オーケストレータイメージ（= 修正11 の「器」）。
#
# ## なぜこのイメージが要るか（学習イメージでは動かない）
#
# Vertex AI Pipelines のステップは**コンテナ実行**である。ステップに
# `python /app/scripts/run_phase.py vertex <stage>` を置くには、そのコンテナが
# run_phase.py・vertex adapter・google-cloud-aiplatform・psycopg を持っていなければ
# ならない。学習イメージは依存最小の設計（core + platforms/neon だけ・クラウド SDK
# 無し）なので**持てない** —— 2026-08-02 の実投入で exit 2 で落ちて確定した事実。
#
# 学習イメージに統合しない理由: 学習イメージは Tier A 3基盤共通で、かつ Tier B の
# 依存制約（Anaconda channel / ML Runtime）と噛み合う最小構成を守っている。
# オーケストレータの依存（aiplatform 等）を足すと、その担保が壊れる。
# **「学習の器」と「オーケストレーションの器」は別物** —— これが Vertex の
# step = コンテナ実行という制約から逆算した帰結（02_architecture「設計の導出順序」）。
#
# ## ビルド
#
#   docker build -f docker/orchestrator/Dockerfile \
#     --build-arg CODE_REVISION="$(git rev-parse HEAD)" -t mcml-orchestrator:latest .
#
# CODE_REVISION 必須の理由は training と同じ（コンテナ内に .git は無い。
# 未指定だと adapter 側の記録が例外で落ちる）。

FROM python:3.12-slim

# libgomp1: run_phase の predict 経路が core（LightGBM 依存）を import するため。
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ARG CODE_REVISION
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONPATH=/app/src \
    CODE_REVISION=${CODE_REVISION}

WORKDIR /app

# オーケストレータの依存 = core の依存 + aiplatform（vertex adapter）+ psycopg（Neon 記録）
# + pyyaml（env/config.yaml の読み取り）。
# **pyyaml を忘れると `設定エラー: pyyaml が無い` で exit 2 になる**（2026-08-02 実測）。
# 学習イメージは config.yaml を読まないので不要 —— この差が器を分ける理由の1つ。
COPY pyproject.toml README.md ./
RUN uv pip install --system --no-cache \
        "lightgbm>=4.6,<4.7" \
        "scikit-learn>=1.8,<1.9" \
        "pandas>=2.3,<2.4" \
        "pyarrow>=21.0,<22.0" \
        "psycopg[binary]>=3.2,<3.3" \
        "google-cloud-aiplatform>=1.163,<1.164" \
        "pyyaml>=6.0,<7.0"

# run_phase が factory 経由で5 adapter モジュールを import するため src/platforms は
# 全部入れる（SDK は遅延 import なので aiplatform 以外の SDK は不要）。
COPY src /app/src
COPY scripts/run_phase.py /app/scripts/run_phase.py
COPY env/config.yaml /app/env/config.yaml
COPY data/california_housing.parquet /app/data/california_housing.parquet

# ENTRYPOINT は指定しない。ステップ側が command を明示する
# （`python /app/scripts/run_phase.py vertex <stage>`）。
