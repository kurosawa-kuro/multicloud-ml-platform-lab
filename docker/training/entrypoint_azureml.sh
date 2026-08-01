#!/usr/bin/env bash
# Azure ML Command Job 用シム。
#
# Azure ML の契約: command job の inputs / outputs マウントパス / 引数は自由。
# パスはジョブ定義側の `${{inputs.data}}` / `${{outputs.model}}` で解決され、
# 実行時には **実パスに置換された引数** として渡ってくる。
# したがってこのシムは3基盤で最も薄く、引数をそのまま流すだけでよい。
#
# 全シムは最終的に必ず同じコマンドへ落とす:
#   python -m core.ml.cli --input "$INPUT_DIR" --output "$MODEL_DIR" --params "$PARAMS_JSON"
#
# 呼び出し側は src/platforms/azureml/adapter.py（command の command 文字列）:
#   bash /app/entrypoint_azureml.sh --input ${{inputs.data}} --output ${{outputs.model}} \
#     --params '<json>'
#
# exit code: 0=成功 / 1=学習失敗（core.ml.cli 由来）/ 2=引数・環境不備
set -euo pipefail

INPUT_DIR=""
MODEL_DIR=""
PARAMS_JSON="{}"
RUN_ID=""
ATTEMPT="1"

while [ $# -gt 0 ]; do
  case "$1" in
    --input)
      INPUT_DIR="${2:-}"
      shift 2
      ;;
    --output)
      MODEL_DIR="${2:-}"
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
      echo "entrypoint_azureml: 未知の引数: $1" >&2
      exit 2
      ;;
  esac
done

if [ -z "$INPUT_DIR" ] || [ -z "$MODEL_DIR" ]; then
  echo "entrypoint_azureml: --input と --output が必要（ジョブ定義のマウント宣言を確認）" >&2
  exit 2
fi

mkdir -p "$MODEL_DIR"

# exec しないのは、学習の**後**に ml_runs を1行残すため（下の job_record）。
started=$SECONDS
set +e
python -m core.ml.cli --input "$INPUT_DIR" --output "$MODEL_DIR" --params "$PARAMS_JSON" \
  ${RUN_ID:+--run-id "$RUN_ID"}
status=$?
set -e

# ジョブの中から ml_runs を1行残す（write_path の一次データ）。telemetry は非致命。
python -m platforms.neon.job_record \
  --platform azureml \
  --output "$MODEL_DIR" \
  ${RUN_ID:+--run-id "$RUN_ID"} \
  --attempt "$ATTEMPT" \
  --exit-code "$status" \
  --duration-seconds "$((SECONDS - started))" || true

exit "$status"
