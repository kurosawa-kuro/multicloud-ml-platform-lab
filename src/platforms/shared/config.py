"""adapter 設定の解決。

**値の出所を2系統に分ける**のがこのモジュールの目的:

    人が決める値      env/config.yaml（コミット対象）
                      region / マシンサイズ / 名前 / データパス
    apply が決める値  terraform output -json（artifacts/<env>.outputs.json）
                      バケット名 / account id / workspace 名 / role ARN / job id

apply が決める値を手書きすると必ず腐る（`env/project.yaml` が別マシンのパスのまま
残っていたのが実例）。だから **outputs を正**とし、config.yaml は既定値だけを持つ。

解決順（後のものが勝つ）:

    dataclass 既定 < env/config.yaml < terraform outputs < 環境変数

環境変数は `MCML_<PLATFORM>_<FIELD>`（例: `MCML_VERTEX_REGION`）。
秘密は**ここでは扱わない**。各 SDK が資格情報チェーン（Doppler / ADC / az login）から
自分で解決する（docs/runbooks/credentials.md）。

使い方:

    settings = load_settings()                 # env/config.yaml + artifacts/*.outputs.json
    adapter = VertexAdapter(settings.vertex(), sink=sink)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from core.telemetry.schemas import Platform
from platforms.azureml.adapter import AzureMlConfig
from platforms.databricks.adapter import DatabricksConfig
from platforms.sagemaker.adapter import SageMakerConfig
from platforms.snowflake.adapter import SnowflakeConfig
from platforms.vertex.adapter import VertexConfig

ENV_PREFIX = "MCML"
DEFAULT_CONFIG_PATH = Path("env/config.yaml")
DEFAULT_OUTPUTS_DIR = Path("artifacts")

# terraform output 名 → dataclass のフィールド名。
# 出力名を変えたらここも直す（outputs.tf とこの表が唯一の対応関係）。
OUTPUT_FIELD_MAP: dict[Platform, dict[str, str]] = {
    Platform.VERTEX: {
        "project_id": "project",
        "region": "region",
        "gcs_bucket": "bucket",
        "vertex_service_account_email": "service_account",
        "vertex_endpoint_display_name": "endpoint_display_name",
    },
    Platform.SAGEMAKER: {
        "region": "region",
        "s3_bucket": "bucket",
        "sagemaker_execution_role_arn": "execution_role_arn",
        "model_package_group_name": "model_package_group_name",
        "endpoint_name": "endpoint_name",
    },
    Platform.AZUREML: {
        "subscription_id": "subscription_id",
        "resource_group_name": "resource_group",
        "workspace_name": "workspace_name",
        "compute_cluster_name": "compute_cluster",
    },
    Platform.DATABRICKS: {
        "catalog_name": "catalog",
        "schema_name": "schema",
        "serving_endpoint_name": "serving_endpoint_name",
    },
    Platform.SNOWFLAKE: {
        "database_name": "database",
        "schema_name": "schema",
        "warehouse_name": "warehouse",
        # adapter が名乗るロール。**Terraform provider の SNOWFLAKE_ROLE
        # （ACCOUNTADMIN）とは別物**（SnowflakeConfig.role のコメント参照）。
        "role_name": "role",
        "stage_name": "stage",
    },
}

# terraform 環境ディレクトリ名（artifacts/<env>.outputs.json の <env>）
OUTPUTS_STEM: dict[Platform, str] = {
    Platform.VERTEX: "gcp-dev",
    Platform.SAGEMAKER: "aws-dev",
    Platform.AZUREML: "azure-dev",
    Platform.DATABRICKS: "dbx-dev",
    Platform.SNOWFLAKE: "sf-dev",
}

CONFIG_SECTION: dict[Platform, str] = {
    Platform.VERTEX: "vertex",
    Platform.SAGEMAKER: "sagemaker",
    Platform.AZUREML: "azureml",
    Platform.DATABRICKS: "databricks",
    Platform.SNOWFLAKE: "snowflake",
}

CONFIG_CLASS: dict[Platform, type] = {
    Platform.VERTEX: VertexConfig,
    Platform.SAGEMAKER: SageMakerConfig,
    Platform.AZUREML: AzureMlConfig,
    Platform.DATABRICKS: DatabricksConfig,
    Platform.SNOWFLAKE: SnowflakeConfig,
}

# `common:` に置いた設定のうち、その基盤には**構造上あり得ない**もの。
#
# `_resolve()` は dataclass に無いキーを捨てる。捨てること自体は正しいが、
# **黙って捨てると「共通設定」が嘘になる**（実例: `common.use_spot: true` が
# Vertex / SageMaker にしか効いておらず、Azure は Terraform 側の
# `vm_priority` が別管理だと気付けなかった。2026-08-01）。
#
# そこでここに列挙したものだけを「意図的な非適用」として許し、
# **列挙に無いキーが捨てられたら ConfigError で落とす**。
# 新しい common キーを足したら、この表も更新しないと通らない。
COMMON_KEY_NOT_APPLICABLE: dict[str, frozenset[Platform]] = {
    # Tier B に Spot の概念が無い（Databricks は serverless、Snowflake は warehouse）
    "use_spot": frozenset({Platform.DATABRICKS, Platform.SNOWFLAKE}),
    # データの指し方が違う: Azure は data_uri（azureml:// 完全パス）、
    # Snowflake は source_table（カタログ内のテーブル名）
    "data_prefix": frozenset({Platform.AZUREML, Platform.SNOWFLAKE}),
}

# `common:` のうち各 platform dataclass へ流し込むキー。
COMMON_KEYS: tuple[str, ...] = ("data_prefix", "use_spot")


class ConfigError(RuntimeError):
    """設定が足りない・壊れている。原因が分かる文言にして投げる。"""


def load_yaml(path: Path) -> dict[str, Any]:
    """env/config.yaml を読む。pyyaml が無い環境では理由を明示して落とす。"""
    try:
        import yaml  # noqa: PLC0415 - 設定読み込み時だけ必要
    except ModuleNotFoundError as exc:  # pragma: no cover - 依存欠落時のみ
        raise ConfigError(
            "pyyaml が無い。`uv sync --extra config` で入れる（core には入れない）"
        ) from exc

    if not path.exists():
        raise ConfigError(f"設定ファイルが無い: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_outputs(path: Path) -> dict[str, Any]:
    """`terraform output -json` の生成物を平らな dict にする。

    出力は `{"key": {"value": ..., "type": ...}}` の形なので value を取り出す。
    ファイルが無いのは異常ではない（apply 前は存在しない）。
    """
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    flattened: dict[str, Any] = {}
    for key, entry in raw.items():
        flattened[key] = entry.get("value") if isinstance(entry, dict) else entry
    return flattened


@dataclass(frozen=True)
class Settings:
    """解決済みの設定。各 `*()` が adapter へ渡す dataclass を組む。"""

    config: dict[str, Any]
    outputs: dict[Platform, dict[str, Any]]
    environ: dict[str, str]

    # --- 各基盤 -----------------------------------------------------------

    def vertex(self) -> VertexConfig:
        values = self._resolve(Platform.VERTEX)
        prefix = self.outputs.get(Platform.VERTEX, {}).get("container_image_prefix")
        self._apply_image_uris(values, prefix)
        return _build(VertexConfig, values, Platform.VERTEX)

    def sagemaker(self) -> SageMakerConfig:
        values = self._resolve(Platform.SAGEMAKER)
        repos = self.outputs.get(Platform.SAGEMAKER, {}).get("ecr_repository_urls") or {}
        tag = self._image_tag()
        if isinstance(repos, dict):
            if repos.get("training"):
                values.setdefault("training_image_uri", f"{repos['training']}:{tag}")
            if repos.get("serving"):
                values.setdefault("serving_image_uri", f"{repos['serving']}:{tag}")
        return _build(SageMakerConfig, values, Platform.SAGEMAKER)

    def azureml(self) -> AzureMlConfig:
        values = self._resolve(Platform.AZUREML)
        registry = self.outputs.get(Platform.AZUREML, {}).get("container_registry_login_server")
        self._apply_image_uris(values, registry)
        return _build(AzureMlConfig, values, Platform.AZUREML)

    def databricks(self) -> DatabricksConfig:
        values = self._resolve(Platform.DATABRICKS)
        return _build(DatabricksConfig, values, Platform.DATABRICKS)

    def snowflake(self) -> SnowflakeConfig:
        values = self._resolve(Platform.SNOWFLAKE)
        return _build(SnowflakeConfig, values, Platform.SNOWFLAKE)

    def for_platform(self, platform: Platform) -> Any:
        """比較スクリプトが5基盤を同じ形で回すための入口。"""
        builders = {
            Platform.VERTEX: self.vertex,
            Platform.SAGEMAKER: self.sagemaker,
            Platform.AZUREML: self.azureml,
            Platform.DATABRICKS: self.databricks,
            Platform.SNOWFLAKE: self.snowflake,
        }
        return builders[platform]()

    # --- 解決ロジック -----------------------------------------------------

    def _resolve(self, platform: Platform) -> dict[str, Any]:
        """dataclass 既定 < config.yaml < terraform outputs < 環境変数。"""
        allowed = {f.name for f in fields(CONFIG_CLASS[platform])}
        values: dict[str, Any] = {}

        common = self.config.get("common") or {}
        for key in COMMON_KEYS:
            if key not in common:
                continue
            if key in allowed:
                values[key] = common[key]
                continue
            # 捨てる前に、それが設計上の非適用かを確かめる（黙殺の禁止）
            if platform not in COMMON_KEY_NOT_APPLICABLE.get(key, frozenset()):
                raise ConfigError(
                    f"common.{key} が {platform.value} に届かない。"
                    f"{CONFIG_CLASS[platform].__name__} にフィールドを足すか、"
                    f"構造上あり得ないなら platforms/config.py の "
                    f"COMMON_KEY_NOT_APPLICABLE[{key!r}] に {platform.value} を追加する"
                )

        section = (self.config.get("platforms") or {}).get(CONFIG_SECTION[platform]) or {}
        values.update({k: v for k, v in section.items() if k in allowed})

        outputs = self.outputs.get(platform, {})
        for output_key, field_name in OUTPUT_FIELD_MAP[platform].items():
            if field_name in allowed and outputs.get(output_key) not in (None, ""):
                values[field_name] = outputs[output_key]

        values.update(self._env_overrides(platform, allowed))
        return values

    def _env_overrides(self, platform: Platform, allowed: set[str]) -> dict[str, Any]:
        prefix = f"{ENV_PREFIX}_{platform.value.upper()}_"
        overrides: dict[str, Any] = {}
        for key, raw in self.environ.items():
            if not key.startswith(prefix):
                continue
            field_name = key[len(prefix) :].lower()
            if field_name in allowed:
                overrides[field_name] = raw
        return overrides

    def _image_tag(self) -> str:
        return str((self.config.get("common") or {}).get("image_tag", "latest"))

    def _apply_image_uris(self, values: dict[str, Any], registry_prefix: Any) -> None:
        """registry の prefix から training / serving のイメージ URI を組む。

        prefix は terraform outputs 由来（Artifact Registry / ACR）。タグだけが
        人の決める値なので config.yaml の common.image_tag を使う。
        """
        if not registry_prefix:
            return
        tag = self._image_tag()
        values.setdefault("training_image_uri", f"{registry_prefix}/training:{tag}")
        values.setdefault("serving_image_uri", f"{registry_prefix}/serving:{tag}")


def load_settings(
    config_path: Path | None = None,
    outputs_dir: Path | None = None,
    environ: dict[str, str] | None = None,
) -> Settings:
    """設定一式を読み込む。apply 前でも（outputs が無くても）落ちない。"""
    config = load_yaml(config_path or DEFAULT_CONFIG_PATH)
    directory = outputs_dir or DEFAULT_OUTPUTS_DIR
    outputs = {
        platform: load_outputs(directory / f"{stem}.outputs.json")
        for platform, stem in OUTPUTS_STEM.items()
    }
    return Settings(config=config, outputs=outputs, environ=dict(environ or os.environ))


def _build(config_class: type, values: dict[str, Any], platform: Platform) -> Any:
    """dataclass を組む。必須フィールドの欠落は「どこで埋めるか」を添えて落とす。"""
    typed = {name: _coerce(config_class, name, value) for name, value in values.items()}
    try:
        return config_class(**typed)
    except TypeError as exc:
        raise ConfigError(
            f"{platform.value} の設定が足りない: {exc}. "
            f"terraform apply 後に `terraform -chdir=infra/environments/{OUTPUTS_STEM[platform]} "
            f"output -json > artifacts/{OUTPUTS_STEM[platform]}.outputs.json` を実行するか、"
            f"env/config.yaml か環境変数 {ENV_PREFIX}_{platform.value.upper()}_* で指定する"
        ) from exc


def _coerce(config_class: type, name: str, value: Any) -> Any:
    """環境変数は文字列で来るので dataclass の型注釈に合わせて戻す。"""
    if not isinstance(value, str):
        return value
    annotation = next((f.type for f in fields(config_class) if f.name == name), None)
    text = str(annotation)
    if "bool" in text:
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if "int" in text and "str" not in text:
        return int(value)
    if "float" in text and "str" not in text:
        return float(value)
    return value
