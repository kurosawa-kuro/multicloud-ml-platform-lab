"""Resolve Terraform input variables from config.yaml and the environment.

Terraform inputs used to be the **third** place settings lived. `env/config.yaml`
held "values a human decides", `artifacts/*.outputs.json` held "values apply
decides", and everything Terraform needed on the way *in* was exported by hand
in a shell, documented only in the runbooks:

    export TF_VAR_job_principal=...        # docs/runbooks/動作検証-databricks.md
    export TF_VAR_create_catalog=false     # (four of these, or ① fails)
    export TF_VAR_budget_notification_email=...

Nothing validated them, so a forgotten export surfaced as a cloud-side failure.
This module makes the two documented sources authoritative for Terraform too:

    config.yaml `terraform.<env>`  values a human decides (committed)
    environment variable          secrets and personal identifiers (Doppler)

Anything still missing fails **before** terraform starts, naming what to set.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Terraform environment directory names under infra/environments/.
ENVIRONMENTS: tuple[str, ...] = ("gcp-dev", "aws-dev", "azure-dev", "dbx-dev", "sf-dev")


class TerraformVarError(RuntimeError):
    """A required Terraform input could not be resolved. Says where to set it."""


@dataclass(frozen=True)
class VarSpec:
    """One Terraform input variable and where its value comes from.

    `env_var` marks values that must not be committed: personal identifiers
    (emails, usernames) and secrets. Everything else belongs in config.yaml.
    """

    name: str
    env_var: str | None = None
    required: bool = True
    note: str = ""


# Per environment, the inputs that have no usable Terraform default.
# Derived values (wheel_path, compute_cluster_vm_priority) are computed in
# `resolve()` from config.yaml rather than being restated here.
VAR_SPECS: dict[str, tuple[VarSpec, ...]] = {
    "gcp-dev": (
        VarSpec(
            "project_id",
            env_var="GOOGLE_CLOUD_PROJECT",
            note="実行プロジェクト ID。SDK 標準名の env をそのまま使う（Doppler）",
        ),
        VarSpec(
            "vertex_submitter_email",
            env_var="MCML_TF_VERTEX_SUBMITTER_EMAIL",
            note="actAs binding の付与先。`gcloud config get-value account`",
        ),
        VarSpec(
            "billing_account_id",
            env_var="MCML_TF_BILLING_ACCOUNT_ID",
            note="予算アラート用の請求先アカウント。**必須**（無いとガードレールが無くなる）",
        ),
    ),
    "aws-dev": (
        VarSpec(
            "budget_notification_email", env_var="MCML_TF_BUDGET_EMAIL", note="予算アラートの通知先"
        ),
    ),
    "azure-dev": (
        VarSpec(
            "subscription_id",
            env_var="MCML_TF_AZURE_SUBSCRIPTION_ID",
            required=False,
            note="空なら az CLI の既定サブスクリプション",
        ),
        VarSpec(
            "budget_notification_email", env_var="MCML_TF_BUDGET_EMAIL", note="予算アラートの通知先"
        ),
    ),
    "dbx-dev": (
        VarSpec(
            "job_principal",
            env_var="MCML_TF_DBX_JOB_PRINCIPAL",
            note="grants の付与先。空だと grants を1つも作らない",
        ),
    ),
    "sf-dev": (
        VarSpec(
            "grant_to_user",
            env_var="MCML_TF_SF_GRANT_TO_USER",
            note="ロールを付ける Snowflake ユーザー名",
        ),
        VarSpec(
            "neon_host",
            env_var="MCML_TF_NEON_HOST",
            required=False,
            note="External Access Integration 用。トライアルでは不要",
        ),
        VarSpec(
            "neon_secret_string",
            env_var="MCML_TF_NEON_SECRET_STRING",
            required=False,
            note="**秘密**。config.yaml へ置かない",
        ),
    ),
}


def _config_section(config: dict[str, Any], env: str) -> dict[str, Any]:
    return ((config.get("terraform") or {}).get(env) or {}) if config else {}


def _as_terraform_literal(value: Any) -> str:
    """Render a YAML scalar the way `-var name=value` expects it."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def derived_vars(config: dict[str, Any], env: str) -> dict[str, str]:
    """Values computed from settings that already exist elsewhere.

    Keeping these derived is the point of 修正03: the same fact must not be
    written twice.
    """
    derived: dict[str, str] = {}
    common = (config.get("common") or {}) if config else {}

    if env == "azure-dev" and "use_spot" in common:
        # Azure alone controls Spot through the compute cluster, not the job.
        # Deriving it here keeps `common.use_spot` the single source.
        derived["compute_cluster_vm_priority"] = (
            "LowPriority" if common["use_spot"] else "Dedicated"
        )

    if env == "dbx-dev":
        from platforms.shared.packaging_names import wheel_filename  # noqa: PLC0415

        section = _config_section(config, env)
        catalog = section.get("catalog_name")
        schema = section.get("schema_name")
        if catalog and schema:
            volume = ((config.get("platforms") or {}).get("databricks") or {}).get(
                "volume", "artifacts"
            )
            derived["wheel_path"] = f"/Volumes/{catalog}/{schema}/{volume}/dist/{wheel_filename()}"

    return derived


def resolve(env: str, config: dict[str, Any], environ: dict[str, str]) -> dict[str, str]:
    """Terraform inputs for `env`, or raise naming every unresolved one."""
    if env not in VAR_SPECS:
        return {}

    resolved: dict[str, str] = {}
    missing: list[str] = []
    section = _config_section(config, env)

    for key, value in section.items():
        resolved[key] = _as_terraform_literal(value)

    for spec in VAR_SPECS[env]:
        if spec.name in resolved:
            continue
        value = environ.get(spec.env_var or "", "")
        if value:
            resolved[spec.name] = value
        elif spec.required:
            missing.append(f"  {spec.name}: 環境変数 {spec.env_var} を設定する（{spec.note}）")

    resolved.update(derived_vars(config, env))

    if missing:
        raise TerraformVarError(
            f"{env} の Terraform 変数が解決できない:\n"
            + "\n".join(missing)
            + "\n秘密・個人識別子は Doppler、それ以外は env/config.yaml の terraform 節へ置く"
        )
    return resolved


def as_cli_args(variables: dict[str, str]) -> list[str]:
    """`{"a": "b"}` -> `["-var", "a=b"]`, ordered for reproducible commands."""
    args: list[str] = []
    for key in sorted(variables):
        args.extend(["-var", f"{key}={variables[key]}"])
    return args
