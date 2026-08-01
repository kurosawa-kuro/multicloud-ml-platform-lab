"""Databricks wheel task の entry point（**serverless の中で動くコード**）。

Tier A の entrypoint シム（docker/training/entrypoint_*.sh）に相当する。
`[project.scripts] train = "platforms.databricks.job_main:cli"` から呼ばれ、
学習（core.ml.cli）を回した**後**に ml_runs を1行残す。

## なぜ core.ml.cli を直接 entry point にしないか

ジョブ内記録（platforms.neon.job_record）は platforms 層にあり、
**core は platforms を import しない**（tests/test_core_boundaries.py が pin する境界）。
cli を直接 entry point にすると記録を呼ぶ者がいなくなり、
「成功行はジョブ側が書く」規約（job_record.py の所有者規約）の下では
**成功した Databricks 学習の run が Neon にもどこにも残らない**。

## 到達経路（Tier B の比較データ）

serverless 環境に psycopg は入れていない（wheel の依存にも無い）ため、
job_record は Neon 直 INSERT に失敗して **Volume 上の JSONL に落ちる = collected**。
Neon 接続情報を secret 経由で渡せるようになったら direct に変わりうる
（Phase 3 precheck 項目）。どちらになるかがそのまま比較レポートの1行。

exit code は core.ml.cli の規約（0=成功 / 1=学習失敗 / 2=入力不備）をそのまま返す。
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
import time
from pathlib import Path

from core.ml.cli.__main__ import main as cli_main
from core.telemetry.schemas import Platform
from platforms.neon import job_record

STAGE_TRAIN = "train"
STAGE_REGISTER = "register"

MODEL_FILENAME = "model.txt"
# 登録した版番号を adapter へ返す唯一の経路（ジョブの stdout）。
# adapter 側は UC を引き直して確定させるので、これは人が読むための痕跡。
VERSION_MARKER = "MODEL_VERSION="

# serving コンテナの依存を **明示する**。
#
# 既定（自動推論）だと MLflow が実行環境から `pyarrow==21.0.0` を拾って conda.yaml に
# 書き、serving のイメージビルドで `mlflow 2.21.3 depends on pyarrow<20` と衝突して
# **コンテナ作成が失敗する**（2026-08-01 実測。エンドポイントは十数分かけてから
# DEPLOYMENT_FAILED になる）。Snowflake の登録が PyPI 経路で落ちたのと同じ
# 「サーバー側の依存解決」層の問題で、クライアントは最後まで成功して見える。
#
# 推論に必要なのは booster を読む lightgbm と入力を組む pandas だけ。
# lightgbm は予測値の一致に直結するので **exact pin**（他基盤と同じ 4.6.0）。
SERVING_PIP_REQUIREMENTS: tuple[str, ...] = ("lightgbm==4.6.0", "pandas>=2.3,<2.4")


def build_parser() -> argparse.ArgumentParser:
    """adapter（python_params）と対の引数契約。

    `--stage` で学習と登録を切り替える。**同じ Job 定義を使い回す**
    （Terraform が持つジョブを adapter が起動するだけ、という境界を崩さないため）。
    """
    parser = argparse.ArgumentParser(prog="train", description="Databricks wheel task の入口")
    parser.add_argument("--stage", default=STAGE_TRAIN, choices=[STAGE_TRAIN, STAGE_REGISTER])
    parser.add_argument("--input", dest="input_dir")
    parser.add_argument("--output", dest="output_dir", required=True)
    parser.add_argument("--params", default="{}")
    parser.add_argument("--run-id", dest="run_id")
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--model-name", dest="model_name", help="UC の3階層名（登録先）")
    parser.add_argument("--experiment", help="MLflow 実験のワークスペースパス")
    return parser


def register_model(
    output_dir: Path, model_name: str, run_id: str | None, experiment: str | None = None
) -> str | None:
    """学習済み booster を MLflow 形式で UC へ登録する（**ジョブの中でしかできない**）。

    Unity Catalog の版は SDK / REST では作れず、**MLflow クライアント経由が唯一の経路**
    （公式: "Creating new model versions requires use of the MLflow Python client"）。
    さらに UC の版は **model signature 必須**なので `input_example` を渡して推論させる。

    Tier A（artifact の URI を渡すだけ）との構造差がここに出る。Snowflake が
    「復元済みモデル + 入力サンプル + 依存解決」を要求したのと同じ性質で、
    **Tier B は登録がサーバー側のモデル理解を伴う**。

    mlflow は wheel の依存に入れない（serverless にプリインストール済み）。
    入っていなければ ImportError をそのまま失敗として返す —— 握り潰すと
    「登録できない基盤」なのか「配線ミス」なのかが記録から消える。
    """
    import lightgbm as lgb  # noqa: PLC0415 - 登録時のみ必要
    import mlflow  # noqa: PLC0415 - serverless のプリインストールに依存
    import pandas as pd  # noqa: PLC0415

    from core.ml.config.constants import FEATURE_COLUMNS  # noqa: PLC0415

    mlflow.set_registry_uri("databricks-uc")
    # **wheel task には既定の実験が無い**（notebook と違い run の置き場が決まらない）。
    # 設定しないと `RESOURCE_DOES_NOT_EXIST: No experiment was found` で落ちる（実測）。
    if experiment:
        mlflow.set_experiment(experiment)
    # Volume 上のファイルを直接掴ませない（書き込み側で seek 制約を踏んだのと同じ層）。
    # 一度ローカルへ落としてから読む。
    local_model = Path(tempfile.mkdtemp(prefix="mcml-reg-")) / MODEL_FILENAME
    with (output_dir / MODEL_FILENAME).open("rb") as src, local_model.open("wb") as dst:
        shutil.copyfileobj(src, dst)
    booster = lgb.Booster(model_file=str(local_model))
    # 列名・順序は学習と同じ FEATURE_COLUMNS が正。ずれると推論だけ静かに壊れる
    example = pd.DataFrame([dict.fromkeys(FEATURE_COLUMNS, 0.0)])

    with mlflow.start_run(run_name=run_id or "mcml"):
        try:
            info = mlflow.lightgbm.log_model(
                booster,
                name="model",
                input_example=example,
                registered_model_name=model_name,
                pip_requirements=list(SERVING_PIP_REQUIREMENTS),
            )
        except TypeError:
            # MLflow 2 系は name= を受け取らない（3 系で artifact_path から改名された）
            info = mlflow.lightgbm.log_model(
                booster,
                artifact_path="model",
                input_example=example,
                registered_model_name=model_name,
                pip_requirements=list(SERVING_PIP_REQUIREMENTS),
            )

    version = getattr(info, "registered_model_version", None)
    return str(version) if version is not None else None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.stage == STAGE_REGISTER:
        if not args.model_name:
            print("--model-name が無い（登録先の UC 3階層名）")
            return 2
        version = register_model(
            Path(args.output_dir), args.model_name, args.run_id, args.experiment
        )
        print(f"{VERSION_MARKER}{version}")
        return 0

    if not args.input_dir:
        print("--input が無い（学習入力）")
        return 2

    output_dir = Path(args.output_dir)
    # **学習は必ずローカルへ書いてから Volume へコピーする。**
    # UC Volume は FUSE マウントで seek を許さず、pandas の `to_csv` が
    # `OSError: [Errno 29] Illegal seek` で落ちる（2026-08-01 実測。model.txt だけ
    # 書けて metrics / feature_importance が落ちるため、成果物が半端に残る）。
    # Tier A のオブジェクトストレージと違い **Volume は「普通のパス」ではない**。
    local_dir = Path(tempfile.mkdtemp(prefix="mcml-"))
    cli_argv = ["--input", args.input_dir, "--output", str(local_dir), "--params", args.params]
    if args.run_id:
        cli_argv += ["--run-id", args.run_id]

    started = time.perf_counter()
    exit_code = cli_main(cli_argv)
    duration = time.perf_counter() - started

    # 学習の成否に関わらず1行残す（telemetry は非致命 = ここで exit_code を変えない）。
    # **JSONL もローカルへ書く。** Volume へ直接 append すると成果物と同じ
    # `Illegal seek` で落ち、記録だけが静かに消える（2026-08-01 実測）。
    try:
        run = job_record.build_run(
            Platform.DATABRICKS,
            manifest=job_record.read_manifest(local_dir),
            run_id=args.run_id,
            exit_code=exit_code,
            attempt=args.attempt,
            duration_seconds=duration,
            error_excerpt=f"core.ml.cli が exit {exit_code}" if exit_code != 0 else None,
        )
        job_record.record_job_run(run, local_dir)
    except Exception as exc:  # noqa: BLE001 - 記録失敗で学習の結果を壊さない
        print(f"job_main: ml_runs の記録に失敗: {exc}")

    try:
        copy_to_volume(local_dir, output_dir)
    except Exception as exc:  # noqa: BLE001 - 成果物の転送失敗は学習失敗として扱う
        print(f"job_main: 成果物を Volume へ置けなかった: {exc}")
        exit_code = exit_code or 1

    return exit_code


def copy_to_volume(local_dir: Path, volume_dir: Path) -> None:
    """ローカルの成果物を Volume へ流し込む（seek しない書き方で）。"""
    volume_dir.mkdir(parents=True, exist_ok=True)
    for path in sorted(local_dir.rglob("*")):
        if not path.is_file():
            continue
        target = volume_dir / path.relative_to(local_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        with path.open("rb") as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst)


def cli() -> None:
    """wheel の console script 本体。

    **`main()` の戻り値ではジョブは失敗しない。** Databricks の python_wheel_task は
    entry point を関数として呼ぶため、`return 1` は握り潰され **task は SUCCESS になる**
    （2026-08-01 実測: 学習が例外で落ちた run が成功として記録された = 緑の嘘）。
    非ゼロは必ず SystemExit で返す。

    逆に **`SystemExit(0)` は失敗扱いになる**（同日実測: 学習成功なのに
    `Workload failed` / error=`SystemExit: 0`）。成功時は何も送出しない。
    """
    code = main()
    if code:
        raise SystemExit(code)


if __name__ == "__main__":
    cli()
