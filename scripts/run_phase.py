"""フェーズ実行の入口（Golden Path のステップ2〜3を回す）。

docs/04_workflows.md のフェーズ実行ワークフローの実体。Makefile はロジックを
持たずここへ委譲する（`make <cloud>-train` 等）。

    doppler run -- python scripts/run_phase.py vertex train
    doppler run -- python scripts/run_phase.py vertex all
    doppler run -- python scripts/run_phase.py sagemaker teardown

## 何をしないか

- terraform apply / destroy は含めない（scripts/run_terraform.py）。
  静的基盤と ML 実行を混ぜないのが境界の原則（docs/02_architecture.md）。
- 失敗しても例外で落とさない。adapter が failure_class 付きの MlRun を返す契約なので、
  **記録してから** 非ゼロで終わる。1つの失敗で残りの stage を続けないのは、
  register / deploy が前段の成果物に依存するため（partial failure を握り潰さない）。

exit code 規約（.claude/rules/scripts.md）: 0=全 stage 成功 / 1=失敗 run あり / 2=引数・設定不備
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from core.ml.config.constants import FEATURE_COLUMNS
from core.telemetry.schemas import MlRun, Platform, Status
from platforms.shared.config import ConfigError
from platforms.shared.contracts.tracking import artifact_uri_from
from platforms.shared.factory import build_adapter, build_sink, deploy_reference
from platforms.shared.resume import (
    ResumeError,
    latest_artifact_uri,
    latest_model_reference,
    persist_handoff,
)

EXIT_OK = 0
EXIT_RUN_FAILED = 1
EXIT_USAGE = 2

STEPS = ("train", "register", "deploy", "predict", "teardown")

# 複数 stage をまとめて回す指定。**teardown は含めない**（明示的に叩かせる）。
# `resume` は学習をやり直さずに ④⑤ を通す用（`--artifact-uri` と対）。
STEP_SETS: dict[str, tuple[str, ...]] = {
    "all": ("train", "register", "deploy", "predict"),
    "resume": ("register", "deploy", "predict"),
}

DEFAULT_DATASET = Path("data/california_housing.parquet")


def sample_instance(dataset: Path) -> dict[str, float]:
    """1件推論の payload。**学習に使ったのと同じ固定具の1行**を使う。

    手で作った値だと、列名の取り違えに気付けないまま「推論できた」ことになる。
    """
    import pandas as pd  # noqa: PLC0415 - CLI 実行時のみ

    if not dataset.exists():
        raise ConfigError(
            f"{dataset} が無い。先に `make dataset-export` を実行するか --instance で渡す"
        )
    frame = pd.read_parquet(dataset)
    row = frame.iloc[0]
    return {column: float(row[column]) for column in FEATURE_COLUMNS}


def resolve_artifact_uri(train_run: MlRun | None, artifact_uri: str | None) -> str:
    """成果物 URI を決める。同一プロセスの train があればそれ、無ければ明示指定。

    `--artifact-uri` を用意しているのは、**学習し直さずに ④⑤ をやり直す**ため。
    Vertex の Spot は待ちが長く（実測: PENDING 15分）、register の権限を1つ直すたびに
    再学習していては attempt が学習側にも積み上がって friction の計測が濁る。
    """
    if train_run is not None:
        return artifact_uri_from(train_run)
    if artifact_uri:
        return artifact_uri
    raise ConfigError(
        "成果物 URI が無い。`all` で train から通すか、`--artifact-uri gs://...` を渡す"
    )


def resolve_resume_values(
    platform: Platform,
    steps: Sequence[str],
    *,
    artifact_uri: str | None,
    model_ref: str | None,
) -> tuple[str | None, str | None]:
    """`resume` を引数なしで動かすため、前段が残した値を Neon から引く。

    **明示指定が常に勝つ。** 引数を渡した人の意図を推測で上書きしない。
    train を含む実行（`all` / `train`）は同一プロセスで値が繋がるので何も引かない。

    Neon へ届かない環境では引けないが、それは異常ではなく
    「明示指定で回してください」と言えば足りるので、ここでは静かに諦める
    （resume の互換性エラーだけは ResumeError として上へ投げる）。
    """
    if "train" in steps:
        return artifact_uri, model_ref

    needs_artifact = "register" in steps and artifact_uri is None
    needs_model_ref = "register" not in steps and "deploy" in steps and model_ref is None
    if not (needs_artifact or needs_model_ref):
        return artifact_uri, model_ref

    try:
        from platforms.neon.connection import connect  # noqa: PLC0415 - psycopg 依存
    except Exception as exc:  # noqa: BLE001 - psycopg 不在は「引けない」の一種
        print(f"Neon から再開値を引けない（明示指定で回す）: {exc}", file=sys.stderr)
        return artifact_uri, model_ref

    if needs_artifact:
        point = latest_artifact_uri(platform, connect=connect)
        print(f"-- 再開元 train run={point.run_id} artifact={point.value}")
        artifact_uri = point.value
    if needs_model_ref:
        point = latest_model_reference(platform, connect=connect)
        print(f"-- 再開元 register run={point.run_id} model={point.value}")
        model_ref = point.value
    return artifact_uri, model_ref


def run_steps(
    adapter: Any,
    steps: Sequence[str],
    *,
    params: dict[str, Any],
    instance: Callable[[], dict[str, float]],
    artifact_uri: str | None = None,
    model_ref: str | None = None,
    sink: Any | None = None,
) -> list[MlRun]:
    """指定 stage を順に実行する。前段が失敗したらそこで止める。

    `deploy` / `predict` は register が同一プロセスで設定した adapter の状態
    （model_resource_name / model_version）を使うので、`resume` でまとめて回す。
    """
    runs: list[MlRun] = []
    train_run: MlRun | None = None

    for step in steps:
        if step == "train":
            run = adapter.submit_training(params)
            train_run = run
            if run.status is Status.SUCCESS and sink is not None:
                # 受け渡しの永続化は **driver の責務**（adapter/記録契約の外）。
                # collected 系統では persist_handoff が明示的にスキップする（resume.py）
                persist_handoff(run, sink)
        elif step == "register":
            run = adapter.register_model(resolve_artifact_uri(train_run, artifact_uri))
        elif step == "deploy":
            run = adapter.deploy(
                deploy_reference(adapter, train_run, artifact_uri=artifact_uri, model_ref=model_ref)
            )
        elif step == "predict":
            run = adapter.predict_one(instance())
        else:
            run = adapter.teardown()

        runs.append(run)
        print(format_run(step, run))
        if run.status is Status.FAILURE:
            print(f"-- {step} が失敗したので後続を中止（前段の成果物に依存するため）")
            break

    return runs


def format_run(step: str, run: MlRun) -> str:
    duration = f"{run.duration_seconds:.1f}s" if run.duration_seconds is not None else "-"
    # track() は失敗時に必ず failure_class を埋めるが、型としては Optional なので明示的に見る
    failure = (
        f" failure_class={run.failure_class.value}"
        if run.status is Status.FAILURE and run.failure_class is not None
        else ""
    )
    return (
        f"[{run.status.value:7}] {run.platform.value:10} {step:9} "
        f"attempt={run.attempt} {duration}{failure}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_phase")
    parser.add_argument("platform", choices=[p.value for p in Platform])
    parser.add_argument(
        "step",
        choices=[*STEPS, "all", "resume"],
        help="all=train から predict まで / resume=学習済み成果物から register 以降",
    )
    parser.add_argument("--params", default="{}", help="ハイパーパラメータの JSON")
    parser.add_argument("--instance", help="1件推論の payload JSON（既定は固定具の1行目）")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--model-version",
        dest="model_version",
        help="登録済みモデルの版 / リソース名。register をやり直さずに ⑤ を再試行する",
    )
    parser.add_argument(
        "--artifact-uri",
        help="学習済み成果物の URI。`resume` で使う（学習し直さずに ④⑤ をやり直す）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    platform = Platform(args.platform)

    try:
        params = json.loads(args.params)
        if not isinstance(params, dict):
            raise ValueError("--params は JSON オブジェクトであること")
        instance_override = json.loads(args.instance) if args.instance else None
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"入力エラー: {exc}", file=sys.stderr)
        return EXIT_USAGE

    sink = build_sink(platform)
    try:
        adapter = build_adapter(platform, sink=sink)
    except ConfigError as exc:
        print(f"設定エラー: {exc}", file=sys.stderr)
        return EXIT_USAGE

    steps = STEP_SETS.get(args.step, (args.step,))
    try:
        artifact_uri, model_ref = resolve_resume_values(
            platform, steps, artifact_uri=args.artifact_uri, model_ref=args.model_version
        )
    except ResumeError as exc:
        print(f"再開できない: {exc}", file=sys.stderr)
        return EXIT_USAGE

    try:
        runs = run_steps(
            adapter,
            steps,
            params=params,
            instance=lambda: instance_override or sample_instance(args.dataset),
            artifact_uri=artifact_uri,
            model_ref=model_ref,
            sink=sink,
        )
    except ConfigError as exc:
        print(f"設定エラー: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except ValueError as exc:
        # deploy 単独実行など、前段（train / register）の結果が要るのに無いケース。
        # traceback で落とさず「同一実行で回す」ことを案内する（adapter の状態は
        # プロセスを跨いで持てないため、register→deploy は `all` で通しにする）
        print(
            f"実行順の不足: {exc}（`all` で train から通すか、前段と同一実行で回す）",
            file=sys.stderr,
        )
        return EXIT_USAGE

    failed = [r for r in runs if r.status is Status.FAILURE]
    print(f"-- {len(runs)} stage 実行 / 失敗 {len(failed)}")
    if any(step in steps for step in ("deploy",)) and not failed:
        print("⚠️ エンドポイントは常時課金。フェーズ末に teardown と tf-destroy を忘れない")
    return EXIT_RUN_FAILED if failed else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
