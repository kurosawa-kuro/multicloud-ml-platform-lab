# Tier A（Vertex AI / SageMaker AI / Azure ML）共通の学習イメージ。
#
# 3基盤で **同一イメージ** を使うことが Tier A の同一性担保。
# 基盤ごとの実行契約の差は entrypoint シムだけで吸収し、
# ここから先（src/core/ml）は1つに保つ。
#
# libgomp1 は LightGBM の実行に必須（slim イメージには入っていない）。
# 外部 CLI（gcloud / aws / az）を subprocess で呼ぶ設計にしない。
# slim コンテナに入っておらず、入れるとイメージが太る。
#
# ビルド:
#   docker build -f docker/training/Dockerfile \
#     --build-arg CODE_REVISION="$(git rev-parse HEAD)" -t <registry>/training:<tag> .
#
# CODE_REVISION を必ず渡す理由: **コンテナ内に .git は無い**ので実行時に git で
# 解決できない。未指定だと core.ml.config.revision が例外を投げ、run が記録されない
# （比較不能な run を混ぜないための設計。docs/02_architecture.md）。

FROM python:3.12-slim

# libgomp1 = LightGBM の OpenMP ランタイム。これが無いと import 時点で落ちる。
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# uv は公式イメージからコピーする（pip で入れるより速く、層も薄い）
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# 依存だけ先に解決してレイヤを効かせる。
# **入れるのは core の依存だけ**（lightgbm / scikit-learn / pandas / pyarrow）+ psycopg。
# psycopg はジョブ内から Neon へ直接 INSERT する経路（write_path='direct'）用。
# 書き込みコード側は未配線（docs/tasks/02_backlog/仕様準拠監査-2026-08-01.md G1）。
# クラウド SDK は入れない
# = adapter はジョブを投げる側で動くため、学習イメージには不要
# （入れると Tier B の Anaconda channel 制約とも噛み合わなくなる）。
COPY pyproject.toml README.md ./
RUN uv pip install --system --no-cache \
        "lightgbm>=4.6,<4.7" \
        "scikit-learn>=1.8,<1.9" \
        "pandas>=2.3,<2.4" \
        "pyarrow>=21.0,<22.0" \
        "psycopg[binary]>=3.2,<3.3"

# 学習に必要なのは core。加えて **platforms/neon だけ**を入れる。
# ジョブの中から ml_runs を書く（write_path='direct' の一次データ）ために要る
# = platforms.neon.job_record。クラウド SDK を使う adapter 群は入れない
# （ジョブを投げる側で動くものなので学習イメージには不要）。
COPY src/core /app/src/core
COPY src/platforms/__init__.py /app/src/platforms/
COPY src/platforms/neon /app/src/platforms/neon

# entrypoint シムは /app 直下（adapter の ENTRYPOINT_PATH と一致させる）
COPY docker/training/entrypoint_vertex.sh \
     docker/training/entrypoint_sagemaker.sh \
     docker/training/entrypoint_azureml.sh \
     /app/
RUN chmod +x /app/entrypoint_*.sh

# code_revision を焼き込む。ビルド引数 → ENV → core.ml.config.revision が読む。
ARG CODE_REVISION
ENV CODE_REVISION=${CODE_REVISION}

# **root で動かす**（2026-08-01 / Phase 2 実測で非 root を撤回）。
#
# SageMaker は `/opt/ml/{model,output}` を **root 所有で作ってからコンテナを起動する**。
# 非 root（uid 10001）だと学習を完走した後の成果物書き出しで落ち、しかも
# `/opt/ml/output/failure` も書けないので DescribeTrainingJob の FailureReason が空になり、
# **failure_class が実態（container）ではなく sdk に落ちる**——成果物・ml_runs 行・
# 失敗理由の3つが同時に潰れる。実ログ:
#
#   [LightGBM] [Fatal] Model file /opt/ml/model/model.txt is not available for writes
#   /app/entrypoint_sagemaker.sh: line 35: /opt/ml/output/failure: Permission denied
#
# Vertex AI / Azure ML は `/opt/ml` を使わない（出力先は自分で作るローカルパス）ので
# この変更の影響を受けない。Tier A は3基盤で**同一イメージ**が同一性担保なので、
# SageMaker だけ別ビルドにはしない。
# 詳細: docs/tasks/02_backlog/sagemaker-phase-precheck.md R-1

# ENTRYPOINT は指定しない。
# 各基盤のジョブ定義側で entrypoint_<platform>.sh を指定する
# （Vertex: container_spec.command / SageMaker: ContainerEntrypoint /
#   Azure ML: command job の command）。
