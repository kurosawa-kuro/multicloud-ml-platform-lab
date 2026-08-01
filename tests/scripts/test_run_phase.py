"""フェーズ実行の入口（scripts/run_phase.py + platforms/factory.py）の検証。

**実クラウドを叩かない**（tests/fakes の adapter を通す）。
ここが壊れると Golden Path のステップ2〜3 が1コマンドで回らない。

守る不変条件:
  - stage を順に回し、**前段が失敗したら止める**（partial failure を握り潰さない）
  - 失敗しても例外で落とさず、非ゼロで終わる（記録は adapter が済ませている）
  - deploy に渡す値の基盤差（比較材料）を factory が正しく解決する
  - 1件推論の payload は**学習に使った固定具の1行**（手書きの値で誤魔化さない）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from tests.conftest import REPO_ROOT, load_script, make_sample_frame
from tests.fakes import all_cases

from core.ml.config.constants import FEATURE_COLUMNS
from core.telemetry.schemas import MlRun, Platform, Status
from core.telemetry.sinks import JsonlRunSink
from platforms.shared.config import ConfigError
from platforms.shared.factory import ADAPTERS, build_sink, deploy_reference

run_phase = load_script("run_phase")

CASES = {case.platform: case for case in all_cases()}


def make_adapter(platform: Platform, sink: JsonlRunSink, *, failing: bool = False) -> Any:
    case = CASES[platform]
    return case.make_failing(sink) if failing else case.make(sink)


def instance_of(_: Any = None) -> dict[str, float]:
    return {column: 1.0 for column in FEATURE_COLUMNS}


# --- factory -------------------------------------------------------------


def test_all_five_platforms_have_an_adapter() -> None:
    """1基盤でも欠けると比較表に穴が空く。"""
    assert set(ADAPTERS) == set(Platform)


def test_sink_falls_back_to_jsonl_without_neon(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Neon が使えない環境でも記録は残る（後から `make collect` で合流）。"""
    from platforms.neon.connection import POOLED_URI_ENV

    monkeypatch.delenv(POOLED_URI_ENV, raising=False)

    sink = build_sink(Platform.VERTEX, fallback_dir=tmp_path)

    assert isinstance(sink, JsonlRunSink)
    assert sink.path.parent.name == "vertex"  # 基盤ごとに分けて回収する


@pytest.mark.parametrize("platform", list(Platform), ids=lambda p: p.value)
def test_deploy_reference_is_resolved_per_platform(platform: Platform, sink: JsonlRunSink) -> None:
    """deploy へ渡す値の差は隠さない（差そのものが比較材料）。"""
    adapter = make_adapter(platform, sink)
    train = adapter.submit_training({})
    adapter.register_model(CASES[platform].artifact_uri)

    reference = deploy_reference(adapter, train)

    assert reference
    if platform is Platform.SAGEMAKER:
        # Endpoint は Model を、Model は成果物を要求する（器を先に作れない）
        assert reference == train.params["model_artifact_uri"]


def test_sagemaker_deploy_without_training_is_refused(sink: JsonlRunSink) -> None:
    adapter = make_adapter(Platform.SAGEMAKER, sink)

    with pytest.raises(ValueError, match="train"):
        deploy_reference(adapter, None)


def test_unregistered_model_is_refused(sink: JsonlRunSink) -> None:
    """register を飛ばして deploy すると「参照が未解決」で止まること。"""
    adapter = make_adapter(Platform.DATABRICKS, sink)
    train = adapter.submit_training({})

    with pytest.raises(ValueError, match="register_model"):
        deploy_reference(adapter, train)


# --- run_steps: stage の連鎖 ---------------------------------------------


def test_all_steps_run_in_order(sink: JsonlRunSink) -> None:
    adapter = make_adapter(Platform.VERTEX, sink)

    runs = run_phase.run_steps(
        adapter, ["train", "register", "deploy", "predict"], params={}, instance=instance_of
    )

    assert [r.stage.value for r in runs] == ["train", "register", "deploy", "predict"]
    assert all(r.status is Status.SUCCESS for r in runs)


def test_chain_stops_at_the_first_failure(sink: JsonlRunSink) -> None:
    """register / deploy は前段の成果物に依存する。失敗を無視して続けない。"""
    adapter = make_adapter(Platform.VERTEX, sink, failing=True)

    runs = run_phase.run_steps(
        adapter, ["train", "register", "deploy", "predict"], params={}, instance=instance_of
    )

    assert len(runs) == 1
    assert runs[0].status is Status.FAILURE


def test_register_without_training_is_a_configuration_error(sink: JsonlRunSink) -> None:
    adapter = make_adapter(Platform.VERTEX, sink)

    with pytest.raises(ConfigError, match="artifact-uri"):
        run_phase.run_steps(adapter, ["register"], params={}, instance=instance_of)


# --- resume: 学習し直さずに ④⑤ をやり直す --------------------------------


def test_resume_skips_training_and_uses_the_given_artifact(sink: JsonlRunSink) -> None:
    """Spot 待ちの長い基盤で、register の権限を直すたびに再学習しないための経路。

    再学習すると train 側の attempt が積み上がり、permission friction の分母が濁る。
    """
    adapter = make_adapter(Platform.VERTEX, sink)
    uri = "gs://mcml-dev/runs/abc/model"

    runs = run_phase.run_steps(
        adapter,
        run_phase.STEP_SETS["resume"],
        params={},
        instance=instance_of,
        artifact_uri=uri,
    )

    assert [r.stage.value for r in runs] == ["register", "deploy", "predict"]
    assert all(r.status is Status.SUCCESS for r in runs)
    assert runs[0].params["artifact_uri"] == uri


def test_resume_requires_an_artifact_uri(sink: JsonlRunSink) -> None:
    adapter = make_adapter(Platform.VERTEX, sink)

    with pytest.raises(ConfigError, match="artifact-uri"):
        run_phase.run_steps(adapter, run_phase.STEP_SETS["resume"], params={}, instance=instance_of)


def test_step_sets_never_include_teardown() -> None:
    """撤退は必ず明示で叩かせる（まとめ実行に混ぜない）。"""
    for steps in run_phase.STEP_SETS.values():
        assert "teardown" not in steps


def test_sagemaker_deploy_accepts_an_explicit_artifact_uri(sink: JsonlRunSink) -> None:
    """SageMaker だけ deploy が成果物 URI を要求するので resume 経路でも渡せること。"""
    adapter = make_adapter(Platform.SAGEMAKER, sink)

    assert deploy_reference(adapter, None, artifact_uri="s3://b/model.tar.gz") == (
        "s3://b/model.tar.gz"
    )


def test_failure_line_names_the_failure_class(sink: JsonlRunSink) -> None:
    """出力だけ見て iam / quota の切り分けが付くこと。"""
    adapter = make_adapter(Platform.SAGEMAKER, sink, failing=True)

    run = adapter.submit_training({})
    line = run_phase.format_run("train", run)

    assert "failure" in line
    assert run.failure_class is not None
    assert run.failure_class.value in line


def test_success_line_omits_failure_class(sink: JsonlRunSink) -> None:
    adapter = make_adapter(Platform.SAGEMAKER, sink)

    line = run_phase.format_run("train", adapter.submit_training({}))

    assert "failure_class" not in line
    assert "attempt=1" in line


# --- 1件推論の payload ----------------------------------------------------


def test_sample_instance_comes_from_the_fixture(tmp_path: Path) -> None:
    """手書きの値だと列名の取り違えに気付けない。学習と同じ固定具を使う。"""
    dataset = tmp_path / "california_housing.parquet"
    make_sample_frame(rows=5).to_parquet(dataset, index=False)

    instance = run_phase.sample_instance(dataset)

    assert set(instance) == set(FEATURE_COLUMNS)
    assert all(isinstance(v, float) for v in instance.values())


def test_missing_dataset_tells_you_what_to_run(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="dataset-export"):
        run_phase.sample_instance(tmp_path / "absent.parquet")


# --- CLI ------------------------------------------------------------------


def test_bad_params_json_is_a_usage_error() -> None:
    assert run_phase.main(["vertex", "train", "--params", "not-json"]) == run_phase.EXIT_USAGE


def test_params_must_be_an_object() -> None:
    assert run_phase.main(["vertex", "train", "--params", "[1, 2]"]) == run_phase.EXIT_USAGE


def test_missing_configuration_is_a_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """apply 前（terraform outputs が無い）でも例外ではなく exit 2 で返す。"""
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr(run_phase, "build_adapter", _raise_config_error, raising=True)

    assert run_phase.main(["snowflake", "train"]) == run_phase.EXIT_USAGE


def _raise_config_error(*args: Any, **kwargs: Any) -> MlRun:
    raise ConfigError("snowflake の設定が足りない: database")


def test_json_params_reach_the_adapter(sink: JsonlRunSink) -> None:
    adapter = make_adapter(Platform.DATABRICKS, sink)

    runs = run_phase.run_steps(
        adapter, ["train"], params=json.loads('{"num_leaves": 63}'), instance=instance_of
    )

    assert runs[0].params["num_leaves"] == 63


# --- resume の引数なし解決（2026-08-01 追加・修正07）----------------------


def test_train_in_steps_never_looks_up_neon() -> None:
    """`all` / `train` は同一プロセスで値が繋がる。DB を引かない。"""
    run_phase = load_script("run_phase")

    assert run_phase.resolve_resume_values(
        Platform.AZUREML, ("train", "register"), artifact_uri=None, model_ref=None
    ) == (None, None)


def test_explicit_arguments_win_over_lookup() -> None:
    """渡した人の意図を推測で上書きしない。"""
    run_phase = load_script("run_phase")

    resolved = run_phase.resolve_resume_values(
        Platform.AZUREML, ("register", "deploy"), artifact_uri="gs://given", model_ref=None
    )

    assert resolved == ("gs://given", None)


def test_resume_looks_up_the_artifact_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    run_phase = load_script("run_phase")
    monkeypatch.setattr(
        run_phase,
        "latest_artifact_uri",
        lambda platform, connect: type("P", (), {"run_id": "r1", "value": "gs://found"})(),
    )

    artifact_uri, _ = run_phase.resolve_resume_values(
        Platform.VERTEX, ("register", "deploy", "predict"), artifact_uri=None, model_ref=None
    )

    assert artifact_uri == "gs://found"


def test_deploy_without_register_looks_up_the_model_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """register をやり直さずに ⑤ を再試行する経路（カタログに版を増やさない）。"""
    run_phase = load_script("run_phase")
    monkeypatch.setattr(
        run_phase,
        "latest_model_reference",
        lambda platform, connect: type("P", (), {"run_id": "r2", "value": "v3"})(),
    )

    _, model_ref = run_phase.resolve_resume_values(
        Platform.DATABRICKS, ("deploy", "predict"), artifact_uri=None, model_ref=None
    )

    assert model_ref == "v3"
