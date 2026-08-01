"""**ジョブの中から** ml_runs を1行残す（write_path の一次データを作る層）。

docs/02_architecture.md「到達経路: primary = ジョブ内から直接 INSERT /
fallback = JSONL を各基盤ストレージへ出力し `make collect`」の primary 側実装。

## なぜ adapter 側の記録では足りないか

adapter（オーケストレータ = 手元マシン / CI）からの INSERT も `write_path='direct'`
にはなるが、それは**手元マシンの egress** であってジョブの egress ではない。
本ラボの比較軸は「Tier A = 通常 egress / Tier B = 宣言的な統合オブジェクト経由」
（docs/01_requirements.md）なので、**ジョブの中から Neon へ届くか**を測らないと
`failure_class='network'` が一度も観測されず、比較軸が空になる。

## run 行の所有者（重要・5基盤共通の規約）

    submit_training が成功した run 行 … **ジョブ側（このモジュール）が書く**
    submit_training が失敗した run 行 … adapter 側が書く
                                         （投入前に落ちる iam / quota は
                                           ジョブが起動しないので adapter でしか観測できない）

run_id は adapter が発番してジョブへ渡す。attempt も adapter が Neon に問い合わせて
渡す（ジョブ側は Neon へ届かない可能性があるので、そこで数え直せない）。
ml_runs.run_id は主キー + `on conflict do nothing` なので、両者が書いても二重にならない。

## 呼ばれ方

Tier A: entrypoint シムが学習 CLI の**後**に呼ぶ（成功・失敗のどちらでも）。

    python -m platforms.neon.job_record \
      --platform vertex --output "$MODEL_DIR" --run-id "$RUN_ID" \
      --attempt "$ATTEMPT" --exit-code "$status" --duration-seconds "$SECONDS"

Tier B (Snowflake): warehouse に psycopg が無いため、このモジュールは使わず
sproc_handler が JSONL を stage へ出す（= 常に collected。それ自体が結果）。

**telemetry は非致命**（docs/06_error_policy.md）。記録に失敗しても学習ジョブの
終了コードを変えない = このモジュールは常に 0 で終わる。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Protocol

from core.ml.config.revision import code_revision
from core.telemetry.schemas import (
    FailureClass,
    MlRun,
    Platform,
    Stage,
    Status,
    WritePath,
)
from core.telemetry.sinks import JsonlRunSink
from core.telemetry.tracking import PLATFORM_TIER

MANIFEST_FILENAME = "run.json"

# 学習 CLI の exit code（core/ml/cli/__main__.py の規約）→ failure_class。
#
# 推測で細かく分類しない。**未分類のまま記録しない**ことだけが要件で、
# 実際の原因は error_excerpt と基盤側のログから後で手当てする
# （docs/06_error_policy.md「分類できなければ SDK 扱いにする」）。
EXIT_CODE_FAILURE_CLASS: dict[int, FailureClass] = {
    1: FailureClass.SDK,  # 学習失敗（原因はログを見るまで分からない）
    2: FailureClass.DATA,  # 入力契約違反・引数不備
}


class RunWriter(Protocol):
    """記録先。Neon 直・JSONL fallback の両方がこれを満たす。"""

    def record_run(self, run: MlRun) -> WritePath: ...


def read_manifest(output_dir: Path) -> dict[str, Any]:
    """run.json（学習が成功したときだけ存在する）を読む。

    無い / 壊れているのは「学習が最後まで行かなかった」ことを意味するだけなので
    例外にしない。run_id と code_revision は引数・環境変数から解決できる。
    """
    path = output_dir / MANIFEST_FILENAME
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def build_run(
    platform: Platform,
    *,
    manifest: dict[str, Any],
    run_id: str | None = None,
    exit_code: int = 0,
    attempt: int = 1,
    duration_seconds: float | None = None,
    error_excerpt: str | None = None,
) -> MlRun:
    """ジョブの実行結果を ml_runs 1行に落とす。

    code_revision は manifest（= 実際に学習したコードが書いた値）を最優先にする。
    manifest が無い場合だけ環境から解決する。**ここで解決できなければ例外**
    （比較不能な run を Neon に混ぜない。core.ml.config.revision の設計）。
    """
    tier, unification_unit = PLATFORM_TIER[platform]
    resolved_run_id = str(manifest.get("run_id") or run_id or "")
    if not resolved_run_id:
        raise ValueError("run_id が無い（adapter が --run-id を渡していない）")

    status = Status.SUCCESS if exit_code == 0 else Status.FAILURE
    return MlRun(
        run_id=resolved_run_id,
        platform=platform,
        tier=tier,
        unification_unit=unification_unit,
        stage=Stage.TRAIN,
        status=status,
        attempt=attempt,
        duration_seconds=duration_seconds,
        failure_class=FailureClass.NONE
        if status is Status.SUCCESS
        else EXIT_CODE_FAILURE_CLASS.get(exit_code, FailureClass.CONTAINER),
        error_excerpt=error_excerpt if status is Status.FAILURE else None,
        code_revision=str(manifest.get("code_revision") or code_revision()),
        # 実際の到達経路は sink が確定する（下の record_job_run）
        write_path=WritePath.DIRECT,
        metrics=dict(manifest.get("metrics") or {}),
        params=dict(manifest.get("params") or {}),
    )


def record_job_run(
    run: MlRun,
    output_dir: Path,
    *,
    neon_sink: RunWriter | None = None,
    jsonl_sink: RunWriter | None = None,
) -> WritePath | None:
    """Neon 直 INSERT を試し、届かなければ JSONL へ落とす。

    **どちらを使ったかがそのまま比較レポートの1行になる**ので、
    到達不能を握り潰さず、使った経路を戻り値と標準出力に残す。
    両方失敗した場合だけ None（それでもジョブは落とさない）。

    sink は注入可能。psycopg が無い環境でも import できるよう、
    既定の NeonRunSink はこの関数の中で遅延生成する。
    """
    if neon_sink is None:
        try:
            from platforms.neon.run_sink import NeonRunSink  # noqa: PLC0415 - psycopg 依存

            neon_sink = NeonRunSink()
        except Exception as exc:  # noqa: BLE001 - psycopg 不在も「到達不能」の一種
            print(f"job_record: Neon sink を作れない: {exc}", file=sys.stderr)

    if neon_sink is not None:
        try:
            write_path = neon_sink.record_run(run)
            print(f"job_record: recorded run_id={run.run_id} write_path={write_path.value}")
            return write_path
        except Exception as exc:  # noqa: BLE001 - 到達不能は結果であって異常終了ではない
            # 到達できなかったこと自体が比較データ。理由を残して fallback へ。
            print(f"job_record: Neon へ到達できない（JSONL へ退避）: {exc}", file=sys.stderr)

    try:
        sink = jsonl_sink if jsonl_sink is not None else JsonlRunSink(output_dir)
        write_path = sink.record_run(run)
    except Exception as exc:  # noqa: BLE001 - telemetry は非致命
        print(f"job_record: JSONL への退避にも失敗: {exc}", file=sys.stderr)
        return None
    print(f"job_record: recorded run_id={run.run_id} write_path={write_path.value}")
    return write_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="platforms.neon.job_record",
        description="学習ジョブの中から ml_runs へ1行記録する（到達経路の一次データ）",
    )
    parser.add_argument("--platform", required=True, choices=[p.value for p in Platform])
    parser.add_argument(
        "--output",
        dest="output_dir",
        required=True,
        type=Path,
        help="成果物ディレクトリ。run.json を読み、届かなければ JSONL をここへ出す",
    )
    parser.add_argument("--run-id", help="adapter が発番した run_id（run.json が無い時に使う）")
    parser.add_argument("--attempt", type=int, default=1, help="adapter が Neon で数えた試行回数")
    parser.add_argument("--exit-code", type=int, default=0, help="学習 CLI の終了コード")
    parser.add_argument("--duration-seconds", type=float)
    parser.add_argument("--error-excerpt", help="失敗時の要約（秘密情報を入れない）")
    return parser


def main(argv: list[str] | None = None) -> int:
    """常に 0 を返す。**記録の失敗で学習ジョブを失敗にしない**（06_error_policy）。"""
    args = build_parser().parse_args(argv)
    output_dir = args.output_dir

    try:
        run = build_run(
            Platform(args.platform),
            manifest=read_manifest(output_dir),
            run_id=args.run_id,
            exit_code=args.exit_code,
            attempt=args.attempt,
            duration_seconds=args.duration_seconds,
            error_excerpt=args.error_excerpt,
        )
    except Exception as exc:  # noqa: BLE001 - ここで落ちてもジョブの結果は変えない
        print(f"job_record: run を組み立てられない: {exc}", file=sys.stderr)
        return 0

    record_job_run(run, output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
