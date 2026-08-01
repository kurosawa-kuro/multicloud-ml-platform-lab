#!/usr/bin/env bash
# Tier A（Vertex AI / SageMaker AI / Azure ML）共通の推論イメージ起動シム。
#
# **1イメージで3契約**（docs/02_architecture.md）。契約ルートの差は
# FastAPI 側（core/app/api/routes.py）が吸収するので、ここがやるのは
# **モデルの所在の差**だけ:
#
#   Vertex AI  AIP_STORAGE_URI が gs:// のまま来る。予測コンテナに FUSE マウントは
#              無いのでローカルへ取得し MODEL_DIR を設定する
#              （core に GCS クライアントを入れない = predictor.py の契約）
#   SageMaker  /opt/ml/model に基盤が展開済み（predictor の既定パス）。
#              起動時に `serve` という引数を付けて呼ばれるので無視する
#   Azure ML   AZUREML_MODEL_DIR を基盤が設定済み
#
# ポートは 8080（SageMaker が固定で要求する。他2基盤も 8080 で揃える）。
#
# exit code: 1=モデル取得失敗（起動させない）。取得できないまま uvicorn を上げると
# ヘルスチェックだけ通って推論が失敗し、原因究明が遅れる。
set -euo pipefail

PORT="${PORT:-8080}"

# SageMaker は `docker run <image> serve` で起動する。引数は使わない。
if [ "${1:-}" = "serve" ]; then
  shift
fi

if [ -n "${AIP_STORAGE_URI:-}" ]; then
  case "$AIP_STORAGE_URI" in
    gs://*)
      echo "entrypoint: AIP_STORAGE_URI から取得: $AIP_STORAGE_URI"
      python /app/fetch_gcs_model.py --uri "$AIP_STORAGE_URI" --dest /tmp/model
      # MODEL_DIR は resolve_model_dir() の最優先。以降 core はローカルパスだけ見る
      export MODEL_DIR=/tmp/model
      ;;
  esac
fi

exec uvicorn core.app.api.main:app --host 0.0.0.0 --port "$PORT"
