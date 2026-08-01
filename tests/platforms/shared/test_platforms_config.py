"""設定解決の契約テスト。

守りたい不変条件は1つ: **値の出所が混ざらないこと**。

    dataclass 既定 < env/config.yaml < terraform outputs < 環境変数

apply が決める値（バケット名・role ARN・workspace 名）を config.yaml に手書きすると
必ず腐るので、outputs が config.yaml に勝つことをここで固定する。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from tests.conftest import REPO_ROOT

import platforms.shared.config as config_module
from core.telemetry.schemas import Platform
from platforms.shared.config import (
    ConfigError,
    Settings,
    load_outputs,
    load_settings,
)

SECRET_WORDS = ("password", "secret", "token", "api_key", "private_key")


def _leaves(node: Any, prefix: str = "") -> list[tuple[str, Any]]:
    """設定ツリーを (ドット区切りのパス, 葉の値) に平坦化する。"""
    if isinstance(node, dict):
        found: list[tuple[str, Any]] = []
        for key, value in node.items():
            found.extend(_leaves(value, f"{prefix}.{key}" if prefix else str(key)))
        return found
    if isinstance(node, list):
        found = []
        for index, value in enumerate(node):
            found.extend(_leaves(value, f"{prefix}[{index}]"))
        return found
    return [(prefix, node)]


CONFIG: dict[str, Any] = {
    "common": {"data_prefix": "data/california_housing", "image_tag": "abc123", "use_spot": True},
    "platforms": {
        "vertex": {"region": "us-central1", "machine_type": "n1-standard-4"},
        "sagemaker": {"region": "ap-northeast-1", "instance_type": "ml.m5.large"},
        "azureml": {"endpoint_instance_type": "Standard_DS2_v2"},
        "databricks": {"job_name": "mcml_dev_train", "scale_to_zero": True},
        "snowflake": {"stage": "CODE", "procedure_name": "TRAIN"},
    },
}

OUTPUTS: dict[Platform, dict[str, Any]] = {
    Platform.VERTEX: {
        "project_id": "example-gcp-project",
        "region": "us-central1",
        "gcs_bucket": "example-gcp-project-mcml-dev",
        "container_image_prefix": "us-central1-docker.pkg.dev/example-gcp-project/mcml",
        "vertex_service_account_email": (
            "mcml-dev-vertex@example-gcp-project.iam.gserviceaccount.com"
        ),
    },
    Platform.SAGEMAKER: {
        "region": "ap-northeast-1",
        "s3_bucket": "mcml-dev-123456789012",
        "sagemaker_execution_role_arn": "arn:aws:iam::123456789012:role/mcml-dev-sagemaker-exec",
        "ecr_repository_urls": {
            "training": "123.dkr.ecr.ap-northeast-1.amazonaws.com/mcml-dev-training",
            "serving": "123.dkr.ecr.ap-northeast-1.amazonaws.com/mcml-dev-serving",
        },
    },
    Platform.AZUREML: {
        "subscription_id": "sub-1",
        "resource_group_name": "mcml-dev-rg",
        "workspace_name": "mcml-dev-ws-abc123",
        "compute_cluster_name": "mcml-dev-cpu",
        "container_registry_login_server": "mcmldevacr.azurecr.io",
    },
    Platform.DATABRICKS: {"catalog_name": "mcml_dev", "schema_name": "ml"},
    Platform.SNOWFLAKE: {
        "database_name": "MCML_DEV",
        "schema_name": "ML",
        "warehouse_name": "MCML_DEV_WH",
        "role_name": "MCML_DEV_ROLE",
    },
}


def build(environ: dict[str, str] | None = None) -> Settings:
    return Settings(config=CONFIG, outputs=OUTPUTS, environ=environ or {})


def test_outputs_supply_the_values_humans_must_not_handwrite() -> None:
    """apply が決める値は outputs から入る（config.yaml には書かない）。"""
    config = build().vertex()

    assert config.project == "example-gcp-project"
    assert config.bucket == "example-gcp-project-mcml-dev"
    assert config.service_account.endswith("iam.gserviceaccount.com")
    # イメージ URI は「outputs の prefix」×「config.yaml のタグ」で組む
    assert config.training_image_uri == (
        "us-central1-docker.pkg.dev/example-gcp-project/mcml/training:abc123"
    )
    assert config.serving_image_uri.endswith("/serving:abc123")


def test_config_yaml_supplies_the_knobs_humans_choose() -> None:
    config = build().vertex()
    assert config.machine_type == "n1-standard-4"
    assert config.data_prefix == "data/california_housing"
    assert config.use_spot is True


def test_outputs_win_over_config_yaml() -> None:
    """同じキーが両方にある場合、生成物（outputs）が勝つ。"""
    settings = Settings(
        config={"platforms": {"vertex": {"region": "asia-northeast1"}}},
        outputs={
            Platform.VERTEX: {
                **OUTPUTS[Platform.VERTEX],
                "region": "us-central1",
            }
        },
        environ={},
    )
    assert settings.vertex().region == "us-central1"


def test_env_wins_over_everything() -> None:
    settings = build({"MCML_VERTEX_REGION": "asia-northeast1"})
    assert settings.vertex().region == "asia-northeast1"


def test_env_values_are_coerced_to_declared_types() -> None:
    settings = build({"MCML_VERTEX_USE_SPOT": "false", "MCML_VERTEX_TIMEOUT_HOURS": "5"})
    config = settings.vertex()
    assert config.use_spot is False
    assert config.timeout_hours == pytest.approx(5.0)


def test_unknown_env_keys_are_ignored() -> None:
    """dataclass に無いキーで落とさない（他プロジェクトの環境変数と共存する）。"""
    settings = build({"MCML_VERTEX_NOT_A_FIELD": "x", "UNRELATED": "y"})
    assert settings.vertex().region == "us-central1"


def test_all_five_platforms_build_from_the_same_source() -> None:
    settings = build()
    for platform in Platform:
        config = settings.for_platform(platform)
        assert config is not None

    assert settings.sagemaker().execution_role_arn.startswith("arn:aws:iam::")
    assert settings.sagemaker().training_image_uri.endswith("mcml-dev-training:abc123")
    assert settings.azureml().workspace_name == "mcml-dev-ws-abc123"
    assert settings.azureml().serving_image_uri == "mcmldevacr.azurecr.io/serving:abc123"
    assert settings.databricks().catalog == "mcml_dev"
    assert settings.snowflake().warehouse == "MCML_DEV_WH"
    # adapter が名乗るロールは outputs 由来。Terraform provider の SNOWFLAKE_ROLE
    # （ACCOUNTADMIN）を使い回すと Snowflake だけ permission friction が測れない
    assert settings.snowflake().role == "MCML_DEV_ROLE"


def test_missing_required_value_explains_where_to_fill_it() -> None:
    settings = Settings(config=CONFIG, outputs={Platform.VERTEX: {}}, environ={})

    with pytest.raises(ConfigError) as excinfo:
        settings.vertex()

    message = str(excinfo.value)
    assert "vertex" in message
    assert "artifacts/gcp-dev.outputs.json" in message
    assert "MCML_VERTEX_" in message


def test_load_outputs_flattens_terraform_json(tmp_path: Path) -> None:
    path = tmp_path / "gcp-dev.outputs.json"
    path.write_text(
        json.dumps(
            {
                "gcs_bucket": {"sensitive": False, "type": "string", "value": "b"},
                "budget_enabled": {"sensitive": False, "type": "bool", "value": True},
            }
        ),
        encoding="utf-8",
    )

    assert load_outputs(path) == {"gcs_bucket": "b", "budget_enabled": True}


def test_load_outputs_before_apply_is_not_an_error(tmp_path: Path) -> None:
    """apply 前は outputs が無い。それは異常ではない。"""
    assert load_outputs(tmp_path / "missing.json") == {}


def test_load_settings_reads_the_repo_config() -> None:
    """実物の env/config.yaml が読め、5基盤のセクションが揃っていること。"""
    yaml = pytest.importorskip("yaml", reason="pyyaml 未インストール環境ではスキップ")
    assert yaml is not None

    root = REPO_ROOT
    settings = load_settings(
        config_path=root / "env" / "config.yaml",
        outputs_dir=root / "artifacts",
        environ={},
    )

    sections = (settings.config.get("platforms") or {}).keys()
    assert set(sections) == {"vertex", "sagemaker", "azureml", "databricks", "snowflake"}
    # 秘密が紛れ込んでいないこと。
    # **キー名ではなく値を見る**。`create_neon_secret: false` のような
    # 「秘密を作るかどうかのフラグ」は秘密ではないので、名前だけで弾くと
    # 正当な設定が置けなくなる（2026-08-01・修正04 で実際に衝突した）。
    for path, value in _leaves(settings.config):
        if not any(word in path.lower() for word in SECRET_WORDS):
            continue
        assert isinstance(value, bool) or value in (None, ""), (
            f"秘密らしき値が config.yaml にある: {path} = {value!r}（Doppler へ移す）"
        )


# --- common 設定の黙殺禁止（2026-08-01 追加）------------------------------
#
# `_resolve()` は dataclass に無いキーを捨てる。捨てること自体は正しいが、
# 黙って捨てると「5基盤共通設定」が嘘になる（実例: common.use_spot は
# Vertex / SageMaker にしか効いておらず、Azure は Terraform 側の vm_priority が
# 別管理だと気付けなかった）。


def test_use_spot_reaches_every_tier_a_platform() -> None:
    """Tier A 3基盤の設定に use_spot が届くこと。"""
    settings = Settings(config=CONFIG, outputs={p: {} for p in Platform}, environ={})

    for platform in (Platform.VERTEX, Platform.SAGEMAKER, Platform.AZUREML):
        resolved = settings._resolve(platform)
        assert "use_spot" in resolved, f"{platform.value} に common.use_spot が届いていない"


def test_common_key_dropped_without_declaration_is_an_error() -> None:
    """非適用として宣言されていない基盤で common キーが落ちたら ConfigError。"""
    settings = Settings(
        config={"common": {"use_spot": True}, "platforms": {}},
        outputs={p: {} for p in Platform},
        environ={},
    )

    with mock.patch.dict(
        config_module.COMMON_KEY_NOT_APPLICABLE, {"use_spot": frozenset()}, clear=False
    ):
        with pytest.raises(ConfigError, match="common.use_spot"):
            settings._resolve(Platform.DATABRICKS)


def test_declared_non_applicable_key_is_dropped_quietly() -> None:
    """構造上あり得ないと宣言済みのものは、落としてよい（Tier B の use_spot）。"""
    settings = Settings(
        config={"common": {"use_spot": True}, "platforms": {}},
        outputs={p: {} for p in Platform},
        environ={},
    )

    resolved = settings._resolve(Platform.SNOWFLAKE)

    assert "use_spot" not in resolved
