"""Databricks wheel entry point（platforms/databricks/job_main.py）の検証。

**serverless の中で動く側。** Tier A のシムテストに相当する。
学習パイプラインは実際に走らせる（tests/test_sproc_handler.py と同じ理由:
ここをモックすると「entry point から学習が本当に回るか」という検証対象が消える）。

守る不変条件:
  - adapter の python_params と同じ引数契約（--input/--output/--params/--run-id/--attempt）
  - 学習後に ml_runs を1行残す（Neon 不達環境では出力先の JSONL = collected）
  - **学習失敗でも分類付きで記録し、exit code は cli の規約のまま返す**
  - 記録の失敗で学習の結果を壊さない（telemetry は非致命）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from tests.conftest import make_sample_frame

from core.ml.config.constants import FEATURE_COLUMNS
from core.telemetry.sinks import JSONL_FILENAME
from platforms.databricks import job_main

FAST_PARAMS = json.dumps({"num_boost_round": 20, "early_stopping_rounds": 5})


@pytest.fixture
def volume(tmp_path: Path) -> Path:
    """UC Volume の代役（serverless からは通常のファイルパスに見える）。"""
    data_dir = tmp_path / "data" / "california_housing"
    data_dir.mkdir(parents=True)
    make_sample_frame().to_parquet(data_dir / "part-0.parquet", index=False)
    return tmp_path


def read_run_record(output_dir: Path) -> dict[str, Any]:
    lines = (output_dir / JSONL_FILENAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1, "ml_runs の行が1行でない"
    return json.loads(lines[0])


def test_trains_and_records_a_collected_run(volume: Path) -> None:
    """成功時: 成果物 + ml_runs 1行（Neon 不達なので collected）。"""
    output = volume / "runs" / "run-1" / "model"

    code = job_main.main(
        [
            "--input",
            str(volume / "data" / "california_housing"),
            "--output",
            str(output),
            "--params",
            FAST_PARAMS,
            "--run-id",
            "11111111-1111-1111-1111-111111111111",
            "--attempt",
            "2",
        ]
    )

    assert code == 0
    assert (output / "model.txt").exists()
    record = read_run_record(output)
    assert record["run_id"] == "11111111-1111-1111-1111-111111111111"
    assert record["platform"] == "databricks"
    assert (record["tier"], record["unification_unit"]) == ("B", "package")
    assert record["attempt"] == 2
    assert record["status"] == "success"
    assert record["write_path"] == "collected"
    assert "rmse" in record["metrics"]
    assert record["code_revision"]


def test_failed_training_is_recorded_with_the_cli_exit_code(tmp_path: Path) -> None:
    """入力が無い → cli は exit 2。**失敗も1行残してから同じコードで返す**。"""
    output = tmp_path / "model"

    code = job_main.main(
        [
            "--input",
            str(tmp_path / "no-such-dir"),
            "--output",
            str(output),
            "--run-id",
            "22222222-2222-2222-2222-222222222222",
        ]
    )

    assert code == 2
    record = read_run_record(output)
    assert record["status"] == "failure"
    assert record["failure_class"] == "data"  # exit 2 = 入力契約・引数不備
    assert record["error_excerpt"]


def test_recording_failure_does_not_change_the_exit_code(
    volume: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """telemetry は非致命（06_error_policy）。学習が成功なら 0 のまま。"""

    def explode(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("volume write denied")

    monkeypatch.setattr(job_main.job_record, "record_job_run", explode)

    code = job_main.main(
        [
            "--input",
            str(volume / "data" / "california_housing"),
            "--output",
            str(volume / "runs" / "x" / "model"),
            "--params",
            FAST_PARAMS,
        ]
    )

    assert code == 0


def test_argument_contract_matches_the_adapter() -> None:
    """adapter（submit_training の python_params）が渡す引数を全部受けること。

    ここがずれると Databricks だけ起動時に argparse で落ち、
    原因が「基盤の問題」に見えてしまう。
    """
    parser = job_main.build_parser()

    parsed = parser.parse_args(
        [
            "--input",
            "/Volumes/c/s/artifacts/data/california_housing",
            "--output",
            "/Volumes/c/s/artifacts/runs/r/model",
            "--params",
            "{}",
            "--run-id",
            "r",
            "--attempt",
            "3",
        ]
    )

    assert parsed.attempt == 3
    assert parsed.run_id == "r"


# --- ④ 登録（ジョブ内 MLflow）--------------------------------------------


def test_register_stage_logs_the_model_to_unity_catalog(
    volume: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--stage register` は学習済み model.txt を MLflow 形式で UC へ登録する。

    UC の版は SDK / REST では作れず MLflow クライアント経由が唯一の経路なので、
    **登録もジョブの中で動く**。mlflow は wheel の依存に入れない
    （serverless のプリインストールを使う）ため、ここでは差し替えて契約だけ固定する。
    """
    output = volume / "runs" / "run-1" / "model"
    assert (
        job_main.main(
            [
                "--input",
                str(volume / "data" / "california_housing"),
                "--output",
                str(output),
                "--params",
                FAST_PARAMS,
                "--run-id",
                "33333333-3333-3333-3333-333333333333",
            ]
        )
        == 0
    )

    calls: dict[str, Any] = {}

    class FakeMlflow:
        class lightgbm:  # noqa: N801 - mlflow の名前空間に合わせる
            @staticmethod
            def log_model(model: Any, **kwargs: Any) -> Any:
                calls["model"] = model
                calls.update(kwargs)
                return type("Info", (), {"registered_model_version": 9})()

        @staticmethod
        def set_registry_uri(uri: str) -> None:
            calls["registry_uri"] = uri

        @staticmethod
        def start_run(run_name: str | None = None) -> Any:
            calls["run_name"] = run_name

            class Ctx:
                def __enter__(self) -> None:
                    return None

                def __exit__(self, *exc: Any) -> bool:
                    return False

            return Ctx()

    monkeypatch.setitem(__import__("sys").modules, "mlflow", FakeMlflow)

    code = job_main.main(
        [
            "--stage",
            "register",
            "--output",
            str(output),
            "--model-name",
            "mcml_dev.ml.california_housing",
            "--run-id",
            "33333333-3333-3333-3333-333333333333",
        ]
    )

    assert code == 0
    # UC へ登録するには registry_uri を切り替える必要がある（既定はワークスペース registry）
    assert calls["registry_uri"] == "databricks-uc"
    assert calls["registered_model_name"] == "mcml_dev.ml.california_housing"
    # UC の版は signature 必須。入力サンプルの列は学習と同じ FEATURE_COLUMNS が正
    assert list(calls["input_example"].columns) == list(FEATURE_COLUMNS)
    assert calls["model"].num_trees() > 0


def test_register_stage_requires_a_model_name(tmp_path: Path) -> None:
    """登録先が無ければ即 exit 2（ジョブ資源を使ってから落ちない）。"""
    assert job_main.main(["--stage", "register", "--output", str(tmp_path)]) == 2


def test_train_stage_requires_an_input(tmp_path: Path) -> None:
    assert job_main.main(["--output", str(tmp_path)]) == 2


def test_console_script_exits_nonzero_on_failure(tmp_path: Path) -> None:
    """**戻り値ではジョブは落ちない。** python_wheel_task は entry point を関数として
    呼ぶため、`return 1` は握り潰されて task が SUCCESS になる（2026-08-01 実測の緑の嘘）。
    console script は SystemExit で返すこと。
    """
    import sys as _sys

    monkey = pytest.MonkeyPatch()
    monkey.setattr(_sys, "argv", ["train", "--output", str(tmp_path)])
    try:
        with pytest.raises(SystemExit) as excinfo:
            job_main.cli()
    finally:
        monkey.undo()

    assert excinfo.value.code == 2


def test_artifacts_are_copied_into_the_volume(volume: Path) -> None:
    """学習はローカルへ書き、成果物だけ Volume へコピーする。

    Volume（FUSE）へ直接書くと pandas の to_csv が `Illegal seek` で落ち、
    **model.txt だけ在って metrics が無い**半端な成果物が残る（2026-08-01 実測）。
    """
    output = volume / "runs" / "copy" / "model"

    code = job_main.main(
        [
            "--input",
            str(volume / "data" / "california_housing"),
            "--output",
            str(output),
            "--params",
            FAST_PARAMS,
        ]
    )

    assert code == 0
    for name in ("model.txt", "metrics.json", "feature_importance.csv", "run.json"):
        assert (output / name).exists(), f"{name} が Volume に無い"


def test_console_script_is_silent_on_success(volume: Path) -> None:
    """**`SystemExit(0)` はジョブ失敗になる**（2026-08-01 実測）。成功時は送出しない。"""
    import sys as _sys

    monkey = pytest.MonkeyPatch()
    monkey.setattr(
        _sys,
        "argv",
        [
            "train",
            "--input",
            str(volume / "data" / "california_housing"),
            "--output",
            str(volume / "runs" / "ok" / "model"),
            "--params",
            FAST_PARAMS,
        ],
    )
    try:
        assert job_main.cli() is None
    finally:
        monkey.undo()


def test_run_record_is_written_into_the_volume(volume: Path) -> None:
    """JSONL もローカルへ書いてからコピーする（Volume へ直接 append すると Illegal seek）。"""
    output = volume / "runs" / "jsonl" / "model"

    job_main.main(
        [
            "--input",
            str(volume / "data" / "california_housing"),
            "--output",
            str(output),
            "--params",
            FAST_PARAMS,
        ]
    )

    assert read_run_record(output)["status"] == "success"


def test_register_pins_serving_dependencies(volume: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """serving イメージの依存を明示すること。

    自動推論だと実行環境の `pyarrow==21.0.0` が conda.yaml に載り、
    `mlflow depends on pyarrow<20` と衝突して **コンテナ作成が失敗する**
    （2026-08-01 実測。十数分待たされてから DEPLOYMENT_FAILED）。
    """
    output = volume / "runs" / "pin" / "model"
    job_main.main(
        [
            "--input",
            str(volume / "data" / "california_housing"),
            "--output",
            str(output),
            "--params",
            FAST_PARAMS,
        ]
    )

    calls: dict[str, Any] = {}

    class FakeMlflow:
        class lightgbm:  # noqa: N801
            @staticmethod
            def log_model(model: Any, **kwargs: Any) -> Any:
                calls.update(kwargs)
                return type("Info", (), {"registered_model_version": 2})()

        @staticmethod
        def set_registry_uri(uri: str) -> None: ...

        @staticmethod
        def set_experiment(path: str) -> None: ...

        @staticmethod
        def start_run(run_name: str | None = None) -> Any:
            class Ctx:
                def __enter__(self) -> None:
                    return None

                def __exit__(self, *exc: Any) -> bool:
                    return False

            return Ctx()

    monkeypatch.setitem(__import__("sys").modules, "mlflow", FakeMlflow)

    job_main.main(["--stage", "register", "--output", str(output), "--model-name", "c.s.m"])

    assert calls["pip_requirements"] == ["lightgbm==4.6.0", "pandas>=2.3,<2.4"]
    assert not any("pyarrow" in r for r in calls["pip_requirements"])
