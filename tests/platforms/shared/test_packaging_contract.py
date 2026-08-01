"""Tier B の配布物（wheel / stage zip）の契約を pin する。

Tier B は「同一 Python パッケージ」で同一性を担保する。ここが崩れると
**ジョブが起動してから**落ちるため、ローカルで機械的に固定しておく:

  1. `pyproject` の entry point が実在する呼び出し可能オブジェクトを指す
     （Databricks の python_wheel_task が呼ぶ入口）
  2. dist 名が Terraform / config.yaml / DatabricksConfig で**1つに揃っている**
     （ずれると entry point を引けず起動しない = 監査 G2 の断線）
  3. 配布物を作る前に `_stamp.py`（code_revision の唯一の運び手）を要求する
  4. stage zip に **warehouse で import できないもの**を入れない
"""

from __future__ import annotations

import re
import tomllib
import typing
import zipfile
from pathlib import Path

import pytest
from tests.conftest import REPO_ROOT

from platforms.databricks.adapter import DatabricksConfig
from platforms.shared.contracts.packaging import PackagingError, require_code_revision_stamp
from platforms.shared.packaging_names import normalize_distribution, wheel_filename
from platforms.snowflake.adapter import SnowflakeAdapter, SnowflakeConfig

REPO_ROOT = REPO_ROOT
PYPROJECT = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
DBX_MODULE = REPO_ROOT / "infra" / "modules" / "databricks"

# **pyproject から導出する。ここに版を書かない。**
# 以前は `f"{DIST_NAME}-0.1.0-..."` と書いており、config.yaml / adapter /
# Terraform に加えて**このテスト自身が4箇所目の写し**になっていた
# （版を上げると pin する側も直す必要があり、drift 検出にならない）。
DIST_NAME = normalize_distribution(PYPROJECT["project"]["name"])
WHEEL_FILENAME = wheel_filename()


def terraform_default(filename: str, variable: str) -> str:
    """variables.tf の default を1つ読む（Terraform を起動せずに参照側を pin する）。"""
    source = (DBX_MODULE / filename).read_text(encoding="utf-8")
    block = re.search(rf'variable "{variable}" \{{(.*?)\n\}}', source, re.DOTALL)
    assert block, f"{filename} に variable {variable} が無い"
    default = re.search(r'default\s+=\s+"([^"]*)"', block.group(1))
    assert default, f"{variable} に default が無い"
    return default.group(1)


# --- 1. entry point ------------------------------------------------------


def test_wheel_entry_point_resolves_to_a_callable() -> None:
    """`train` が実在すること。無いと Databricks のジョブが起動できない。"""
    import importlib

    target = PYPROJECT["project"]["scripts"]["train"]
    module_name, _, attribute = target.partition(":")

    entry = getattr(importlib.import_module(module_name), attribute)

    assert callable(entry)
    # **戻り値ではなく SystemExit で終了コードを返す契約**。
    # Databricks の python_wheel_task は entry point を関数として呼ぶため、
    # `return 1` は握り潰されて task が SUCCESS になる（2026-08-01 実測の緑の嘘）。
    # 実際に送出することは tests/test_databricks_job_main.py が固定する。
    # （`from __future__ import annotations` で注釈は文字列になるので解決してから見る）
    assert typing.get_type_hints(entry).get("return") is type(None)


def test_entry_point_name_matches_terraform() -> None:
    assert terraform_default("variables.tf", "job_entry_point") in PYPROJECT["project"]["scripts"]


def test_build_backend_is_pinned() -> None:
    """legacy fallback だとビルド環境で dist の中身が変わりうる。"""
    assert PYPROJECT["build-system"]["build-backend"] == "setuptools.build_meta"


# --- 2. dist 名の一致（正本1つ・参照側を全部 pin）------------------------


def test_dist_name_is_consistent_across_the_repo() -> None:
    assert PYPROJECT["project"]["name"].replace("-", "_") == DIST_NAME
    assert terraform_default("variables.tf", "job_package_name") == DIST_NAME
    assert DatabricksConfig(catalog="c", schema="s").wheel_filename == WHEEL_FILENAME
    assert WHEEL_FILENAME in (DBX_MODULE / "main.tf").read_text(encoding="utf-8")


def test_config_yaml_does_not_pin_the_wheel_filename() -> None:
    """config.yaml が wheel 名を再掲しないこと（正本の二重化を戻さない）。

    2026-08-01 まで config.yaml にも版込みで書いてあり、pyproject を上げると
    3箇所を同時に直す必要があった。正本は pyproject 1つ。
    """
    config_yaml = (REPO_ROOT / "env" / "config.yaml").read_text(encoding="utf-8")

    assert "wheel_filename:" not in config_yaml, (
        "env/config.yaml に wheel_filename が戻っている（pyproject から導出する）"
    )


# --- 3. stamp の要求 ------------------------------------------------------


def test_stamp_is_required_before_building(tmp_path: Path) -> None:
    """焼き忘れをローカルで止める（ジョブ起動後に落とさない）。"""
    with pytest.raises(PackagingError, match="stamp-revision"):
        require_code_revision_stamp(tmp_path)


def test_empty_stamp_is_rejected(tmp_path: Path) -> None:
    stamp = tmp_path / "src" / "core" / "ml" / "config" / "_stamp.py"
    stamp.parent.mkdir(parents=True)
    stamp.write_text("# 生成に失敗した残骸\n", encoding="utf-8")

    with pytest.raises(PackagingError):
        require_code_revision_stamp(tmp_path)


def test_repository_stamp_satisfies_the_check() -> None:
    """空振りテストにしない（実リポジトリで通ることを確認する）。"""
    assert require_code_revision_stamp(REPO_ROOT).exists()


# --- 4. stage zip の中身 --------------------------------------------------


def test_stage_package_contains_the_training_code_and_stamp(tmp_path: Path) -> None:
    adapter = SnowflakeAdapter(
        SnowflakeConfig(database="D", schema="S", warehouse="W"),
        sink=None,  # type: ignore[arg-type]
    )

    package = adapter.build_package(REPO_ROOT, tmp_path)

    names = set(zipfile.ZipFile(package).namelist())
    assert package.name == "core_ml.zip"
    # 学習本体 / sproc ハンドラ / code_revision の運び手
    assert "core/ml/pipelines/train_pipeline.py" in names
    assert "platforms/snowflake/sproc_handler.py" in names
    assert "core/ml/config/_stamp.py" in names
    # JSONL 退避（Tier B の到達経路）に要る
    assert "core/telemetry/sinks.py" in names


def test_stage_package_excludes_other_platform_adapters(tmp_path: Path) -> None:
    """warehouse の Anaconda channel に無い SDK を要求するものを入れない。

    入れると sproc の import 時点で落ちる（原因が Snowflake 側に見えて調査が遅れる）。
    """
    adapter = SnowflakeAdapter(
        SnowflakeConfig(database="D", schema="S", warehouse="W"),
        sink=None,  # type: ignore[arg-type]
    )

    names = set(zipfile.ZipFile(adapter.build_package(REPO_ROOT, tmp_path)).namelist())

    for excluded in ("platforms/vertex", "platforms/sagemaker", "platforms/azureml"):
        assert not any(n.startswith(excluded) for n in names), excluded
    assert not any("__pycache__" in n for n in names)


# --- 5. ライブラリ版の一元管理（正本 = pyproject）-------------------------


def declared_core_dependencies() -> dict[str, str]:
    """pyproject の core 依存を {パッケージ名: 制約付き文字列} で返す。"""
    return {spec.split(">=")[0]: spec for spec in PYPROJECT["project"]["dependencies"]}


@pytest.mark.parametrize("package", ["lightgbm", "scikit-learn", "pandas"])
def test_training_image_pins_match_pyproject(package: str) -> None:
    """学習イメージの版が pyproject とずれていないこと。

    Dockerfile は `uv pip install` に版を**直書き**する（pyproject を読ませると
    レイヤキャッシュが効かない）。そのぶん静かに腐るので、ここで機械的に縛る。
    ずれると「同一SHA・同一依存」の前提が崩れ、基盤間で RMSE が変わりうる。
    """
    dockerfile = (REPO_ROOT / "docker" / "training" / "Dockerfile").read_text(encoding="utf-8")

    assert f'"{declared_core_dependencies()[package]}"' in dockerfile


def test_serving_image_pins_match_pyproject() -> None:
    """推論イメージも同じ minor（学習と serving でずれると予測値が変わる）。"""
    dockerfile = (REPO_ROOT / "docker" / "serving" / "Dockerfile").read_text(encoding="utf-8")
    declared = declared_core_dependencies()

    for package in ("lightgbm", "pandas"):  # sklearn は推論に不要なので入れていない
        assert f'"{declared[package]}"' in dockerfile


def test_scikit_learn_upper_bound_is_set_by_snowflake() -> None:
    """sklearn の上限が Tier B（snowflake-ml-python）の制約と整合していること。

    snowflake-ml-python は最新でも `scikit-learn<1.9` を要求する。
    1.9 系へ上げると Snowflake の Model Registry SDK が同居できなくなり、
    Phase 5 が着手不能になる（2026-08-01 に実測）。上げるときは
    snowflake-ml-python 側の制約を先に確認すること。
    """
    assert declared_core_dependencies()["scikit-learn"] == "scikit-learn>=1.8,<1.9"
