"""Vertex AI Pipelines 定義（修正11）の検証。

**クラウドを叩かない。** compile までがローカルで完結するので、
「既存イメージ + 既存 CLI だけで DAG が組める」ことはここで機械的に示せる。

守る不変条件:
  - ステップは `run_phase.py vertex <stage>` を呼ぶ（新しい入口を作らない
    ＝ adapter / tracking / job_record を書き換えずに済む理由そのもの）
  - env は設定と秘密だけを通す（手元の環境をジョブ定義へ丸ごと載せない）
  - キャッシュは既定で無効（命中すると実行していない成功が生まれ、attempt と実態がずれる）
"""

import json
from pathlib import Path

import pytest
import yaml

from platforms.vertex.pipeline import (
    NEON_URI_ENV,
    RUN_PHASE,
    compile_pipeline,
    step_env,
)

IMAGE = "us-central1-docker.pkg.dev/example-gcp-project/mcml/training:latest"


def compiled(tmp_path: Path, environ: dict[str, str], **kwargs: object) -> dict:
    out = tmp_path / "pipeline.yaml"
    compile_pipeline(IMAGE, environ, str(out), **kwargs)  # type: ignore[arg-type]
    return yaml.safe_load(out.read_text(encoding="utf-8"))


def containers(spec: dict) -> list[dict]:
    return [executor["container"] for executor in spec["deploymentSpec"]["executors"].values()]


# --- env の選別 -----------------------------------------------------------


def test_only_config_and_secret_env_are_passed() -> None:
    """手元の環境を丸ごと渡さない。

    ジョブ定義は各基盤のコンソール / Describe API から読めるので、
    無関係な資格情報を載せると読める場所が増える
    （`contracts.tracking.telemetry_env` と同じ注意）。
    """
    selected = step_env(
        {
            NEON_URI_ENV: "postgresql://u:p@h/db",
            "MCML_VERTEX_PROJECT": "example-gcp-project",
            "AWS_SECRET_ACCESS_KEY": "unrelated",
            "SNOWFLAKE_PRIVATE_KEY": "unrelated",
        }
    )

    assert set(selected) == {NEON_URI_ENV, "MCML_VERTEX_PROJECT"}


def test_code_revision_is_never_passed() -> None:
    """コンテナに焼き込んだ値を上書きしない（比較の担保が壊れる）。

    `telemetry_env` が同じ理由で CODE_REVISION を渡さないのと揃える。
    """
    assert "CODE_REVISION" not in step_env({"CODE_REVISION": "deadbeef"})


def test_empty_values_are_dropped() -> None:
    """空文字を渡すと「設定した」と誤認される。未設定として扱う。"""
    assert step_env({"MCML_VERTEX_PROJECT": ""}) == {}


# --- 組み上がる DAG -------------------------------------------------------


def test_steps_invoke_the_existing_cli(tmp_path: Path) -> None:
    """新しい入口を作らないこと。ここが崩れると Core 変更ゼロの前提が崩れる。"""
    spec = compiled(tmp_path, {"MCML_VERTEX_PROJECT": "example-gcp-project"})

    for container in containers(spec):
        assert container["command"] == RUN_PHASE
        assert container["args"][0] == "vertex"
        assert container["image"] == IMAGE


def test_pipeline_has_train_then_register(tmp_path: Path) -> None:
    """deploy / predict は入れない（常時課金。記録が保てるかの確認に不要）。"""
    spec = compiled(tmp_path, {"MCML_VERTEX_PROJECT": "example-gcp-project"})
    tasks = spec["root"]["dag"]["tasks"]

    assert len(tasks) == 2
    stages = sorted(
        task["inputs"]["parameters"]["stage"]["runtimeValue"]["constant"] for task in tasks.values()
    )
    assert stages == ["register", "train"]
    # register は train の後（成果物 URI を Neon から引くため順序が要る）
    dependent = [task for task in tasks.values() if task.get("dependentTasks")]
    assert len(dependent) == 1


def test_selected_env_reaches_the_container(tmp_path: Path) -> None:
    spec = compiled(
        tmp_path,
        {NEON_URI_ENV: "postgresql://u:p@h/db", "MCML_VERTEX_REGION": "us-central1"},
    )

    for container in containers(spec):
        names = {entry["name"] for entry in container["env"]}
        assert names == {NEON_URI_ENV, "MCML_VERTEX_REGION"}


def test_unrelated_secrets_never_reach_the_spec(tmp_path: Path) -> None:
    """選別が漏れていないことを、生成物の全文で確かめる。"""
    spec = compiled(
        tmp_path,
        {"MCML_VERTEX_PROJECT": "example-gcp-project", "AWS_SECRET_ACCESS_KEY": "must-not-appear"},
    )

    assert "must-not-appear" not in json.dumps(spec)


# --- キャッシュ -----------------------------------------------------------


def test_caching_is_disabled_by_default(tmp_path: Path) -> None:
    """キャッシュ命中は「実行していない成功」を作る。

    行が生まれないまま成功扱いになると、`ml_runs` の attempt 連番と実行実態がずれ、
    permission friction の集計が意味を失う。比較ラボにとってキャッシュは害。
    """
    spec = compiled(tmp_path, {"MCML_VERTEX_PROJECT": "example-gcp-project"})

    for task in spec["root"]["dag"]["tasks"].values():
        assert task.get("cachingOptions", {}).get("enableCache") is not True


@pytest.mark.parametrize("enabled", [True, False])
def test_caching_flag_is_reflected(tmp_path: Path, enabled: bool) -> None:
    """既定を変えたときに spec へ効くことも押さえる（既定値だけのテストにしない）。"""
    spec = compiled(
        tmp_path, {"MCML_VERTEX_PROJECT": "example-gcp-project"}, enable_caching=enabled
    )

    for task in spec["root"]["dag"]["tasks"].values():
        assert (task.get("cachingOptions", {}).get("enableCache") is True) is enabled
