#!/usr/bin/env bash
# SageMaker AI Training Job 用シム（BYOC）。
#
# SageMaker の契約は3基盤で最も厳しく、パスが固定されている:
#   /opt/ml/input/data/<channel>              入力
#   /opt/ml/input/config/hyperparameters.json ハイパーパラメータ
#   /opt/ml/model                             モデル出力（S3 へ回収される）
#   /opt/ml/output/failure                    失敗理由（失敗時にここへ書く）
#
# SageMaker は引数を渡さず hyperparameters.json を置くだけなので、
# JSON をここで読んで --params に載せ替えるのがシムの本体。
#
# 全シムは最終的に必ず同じコマンドへ落とす:
#   python -m core.ml.cli --input "$INPUT_DIR" --output "$MODEL_DIR" --params "$PARAMS_JSON"
#
# hyperparameters.json の値は **文字列限定**。そのため adapter 側
# （src/platforms/sagemaker/adapter.py）は params 全体を JSON 文字列にして
# "params" キー1つに畳んで渡す。ここで取り出して型を復元する。
#
# 失敗時は /opt/ml/output/failure に理由を書く。書かないと DescribeTrainingJob に
# 理由が出ず、失敗の分類（failure_class）が推測になる
# = permission friction の一次データが濁る。
#
# exit code: 0=成功 / 1=学習失敗（core.ml.cli 由来）/ 2=引数・環境不備
set -euo pipefail

CHANNEL="${SM_CHANNEL:-training}"
INPUT_DIR="/opt/ml/input/data/${CHANNEL}"
MODEL_DIR="/opt/ml/model"
CONFIG_JSON="/opt/ml/input/config/hyperparameters.json"
FAILURE_FILE="/opt/ml/output/failure"

write_failure() {
  mkdir -p "$(dirname "$FAILURE_FILE")"
  printf '%s\n' "$1" > "$FAILURE_FILE"
  echo "entrypoint_sagemaker: $1" >&2
}

if [ ! -d "$INPUT_DIR" ]; then
  write_failure "入力チャネルが無い: ${INPUT_DIR}（InputDataConfig の ChannelName を確認）"
  exit 2
fi

PARAMS_JSON="{}"
RUN_ID=""
ATTEMPT="1"
if [ -f "$CONFIG_JSON" ]; then
  # hyperparameters["params"] は JSON 文字列。run_id / attempt も同じ経路で来る
  # （SageMaker は引数を渡せないため。値は文字列限定）。
  # 3値を**1行ずつ**受け取る。タブ区切りにすると、bash の read は IFS 空白文字
  # （タブを含む）の連続を1つに畳むため、run_id が空のとき値が1つずれる。
  {
    read -r PARAMS_JSON || true
    read -r RUN_ID || true
    read -r ATTEMPT || true
  } <<EOF
$(python -c '
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    hyperparameters = json.load(f)
raw = hyperparameters.get("params", "{}")
try:
    parsed = json.loads(raw) if isinstance(raw, str) else raw
except json.JSONDecodeError:
    parsed = {}
print(json.dumps(parsed if isinstance(parsed, dict) else {}))
print(hyperparameters.get("run_id", ""))
print(hyperparameters.get("attempt", "1"))
' "$CONFIG_JSON")
EOF
  PARAMS_JSON="${PARAMS_JSON:-{\}}"
  ATTEMPT="${ATTEMPT:-1}"
fi

mkdir -p "$MODEL_DIR"

started=$SECONDS
set +e
python -m core.ml.cli --input "$INPUT_DIR" --output "$MODEL_DIR" --params "$PARAMS_JSON" \
  ${RUN_ID:+--run-id "$RUN_ID"}
status=$?
set -e

# ジョブの中から ml_runs を1行残す（write_path の一次データ）。
# 失敗時も記録してから終わる（失敗こそ permission friction の一次データ）。
# telemetry は非致命 = ここの失敗で学習の終了コードを変えない。
python -m platforms.neon.job_record \
  --platform sagemaker \
  --output "$MODEL_DIR" \
  ${RUN_ID:+--run-id "$RUN_ID"} \
  --attempt "$ATTEMPT" \
  --exit-code "$status" \
  --duration-seconds "$((SECONDS - started))" || true

if [ "$status" -ne 0 ]; then
  write_failure "core.ml.cli が exit ${status} で終了"
  exit "$status"
fi
