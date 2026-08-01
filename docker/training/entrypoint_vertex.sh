#!/usr/bin/env bash
# Vertex AI Custom Job 用シム。
#
# Vertex の契約: AIP_MODEL_DIR に成果物を書く / 引数は自由。
# 引数が自由な分、3基盤の中では最も薄いシムになる。
#
# 全シムは最終的に必ず同じコマンドへ落とす:
#   python -m core.ml.cli --input "$INPUT_DIR" --output "$MODEL_DIR" --params "$PARAMS_JSON"
#
# 呼び出し側は src/platforms/vertex/adapter.py（container_spec.args）。
#   command: ["bash", "/app/entrypoint_vertex.sh"]
#   args   : ["--input", "/gcs/<bucket>/data/...", "--params", "<json>"]
#
# 出力先の解決:
#   AIP_MODEL_DIR は gs:// URI で来る。Vertex の学習コンテナは gs://<bucket> を
#   /gcs/<bucket> に FUSE マウントするので、そのパスへ書き換えてから渡す
#   （core.ml.cli は通常のファイルパスしか扱わない = 5基盤共通に保つため）。
#   流用元 kaggle-bronze-gcp は AIP_MODEL_DIR を使わず --output-uri 方式だったので、
#   ここが唯一の差し替え点。
#
# exit code: 0=成功 / 1=学習失敗（core.ml.cli 由来）/ 2=引数・環境不備
set -euo pipefail

INPUT_DIR=""
PARAMS_JSON="{}"
RUN_ID=""
ATTEMPT="1"

while [ $# -gt 0 ]; do
  case "$1" in
    --input)
      INPUT_DIR="${2:-}"
      shift 2
      ;;
    --params)
      PARAMS_JSON="${2:-{\}}"
      shift 2
      ;;
    --run-id)
      RUN_ID="${2:-}"
      shift 2
      ;;
    --attempt)
      ATTEMPT="${2:-1}"
      shift 2
      ;;
    *)
      echo "entrypoint_vertex: 未知の引数: $1" >&2
      exit 2
      ;;
  esac
done

if [ -z "$INPUT_DIR" ]; then
  echo "entrypoint_vertex: --input が必要（学習データのディレクトリ）" >&2
  exit 2
fi

if [ -z "${AIP_MODEL_DIR:-}" ]; then
  echo "entrypoint_vertex: AIP_MODEL_DIR が無い（CustomJob の base_output_dir 未設定）" >&2
  exit 2
fi

MODEL_DIR="$AIP_MODEL_DIR"
case "$MODEL_DIR" in
  gs://*) MODEL_DIR="/gcs/${MODEL_DIR#gs://}" ;;
esac
mkdir -p "$MODEL_DIR"

# exec しないのは、学習の**後**に ml_runs を1行残すため（下の job_record）。
started=$SECONDS
set +e
python -m core.ml.cli --input "$INPUT_DIR" --output "$MODEL_DIR" --params "$PARAMS_JSON" \
  ${RUN_ID:+--run-id "$RUN_ID"}
status=$?
set -e

# ジョブの中から ml_runs を1行残す（write_path の一次データ）。
# Neon へ届かなければ MODEL_DIR に JSONL を落とし `make collect` で回収する。
# **telemetry は非致命** = ここの失敗で学習の終了コードを変えない。
python -m platforms.neon.job_record \
  --platform vertex \
  --output "$MODEL_DIR" \
  ${RUN_ID:+--run-id "$RUN_ID"} \
  --attempt "$ATTEMPT" \
  --exit-code "$status" \
  --duration-seconds "$((SECONDS - started))" || true

exit "$status"
