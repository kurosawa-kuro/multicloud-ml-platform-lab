"""5基盤横断の残留リソース検出。

`terraform destroy` の後に何が残ったかを列挙し infra_events へ記録する。
「撤退後の残留リソース」は比較軸そのものであり、この結果が
docs/comparison/residual-resources.md の一次データになる。

判定原理（eks-app-mlops-platform-v2 の OrphanCleaner から流用）:
    terraform state に不在 × クラウドに実在 = 孤児

分類:
    FAIL  : 課金が続く / destroy を阻害する（Endpoint / Compute / Warehouse 等）
    WARN  : 残るが軽微（空バケット / ログ / Time Travel 等）
    ERROR : API 無効・権限不足などで検査自体ができなかった
FAIL または ERROR が1つでもあれば exit 1（移植元 destroy_check.py と同じ規約）。

Tier B の残留候補は Tier A と質が違う（Time Travel / Fail-safe /
カタログ内オブジェクト / stage 成果物）。同じ土俵で数えず、`kind` に種別を残す。

移植元: ML/gcp-search-mlops-gke/scripts/ops/destroy_check.py（402行）の
FAIL/WARN/ERROR 分類・API 無効の吸収・exit code 規約。
本ラボは5基盤横断なので、各基盤の列挙関数を差し替え可能な形にしてある
（クライアントは引数注入。実クラウドを叩かないテストを書けるようにするため）。

    doppler run -- python scripts/check_residual.py --platform vertex
    doppler run -- python scripts/check_residual.py --all
    doppler run -- python scripts/check_residual.py --all --record
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

# scripts/ は src/ をパスに持たない前提でも動くよう、遅延 import に寄せる。
PLATFORMS = ("vertex", "sagemaker", "azureml", "databricks", "snowflake")

SEVERITY_FAIL = "FAIL"
SEVERITY_WARN = "WARN"
SEVERITY_ERROR = "ERROR"

# 本ラボが作るリソース名に必ず入る語（infra/modules/*/main.tf の project_name）。
# **クラウドのプロジェクト/アカウントは他用途と共有**なので、これで絞らないと
# 無関係なリソースが「残留」として比較表に載る。
LAB_NAME_PREFIX = "mcml"

# terraform outputs の保存先（apply 直後に生成。destroy 後も「何を作ったか」の記録として残る）
OUTPUTS_PATH = "artifacts/gcp-dev.outputs.json"
AZURE_OUTPUTS_PATH = "artifacts/azure-dev.outputs.json"
DATABRICKS_OUTPUTS_PATH = "artifacts/dbx-dev.outputs.json"

# 「もう無い」と読めるエラー文言。**カタログごと消えている = 残留ゼロ**であって
# 検査不能（ERROR）ではない。区別しないと destroy 成功が毎回 ERROR に見える。
_MISSING_HINTS = ("does not exist", "not found", "no such", "resource_does_not_exist")


def _is_lab_resource(name: str) -> bool:
    """本ラボが作った名前か。

    **大小文字を無視する。** Snowflake は識別子を大文字化して作るため
    （`MCML_DEV` / `MCML_DEV_WH`）、素朴な `in` では1件も一致せず
    絞り込みが無効化される（= 他アカウント資産まで残留に混ざる）。
    """
    return LAB_NAME_PREFIX in str(name).casefold()


def _looks_missing(exc: Exception) -> bool:
    return any(hint in str(exc).casefold() for hint in _MISSING_HINTS)


# 親（リソースグループ / ワークスペース / カタログ）ごと消えているときの API エラーコード。
#
# **親が無い = 中身は定義上ゼロ**であって「調べられなかった」ではない。
# 完全撤収の後にこの検査を回すと、以前は次のように出ていた（2026-08-01 実測）:
#
#   [ERROR] azureml/online_endpoint:   (ResourceGroupNotFound) ...
#   [ERROR] azureml/registered_model:  (ParentResourceNotFound) ...
#
# これは本モジュールの判定原理（「調べられなかった」と「無かった」は別）の裏返しで、
# **最も確実な「無い」を「不明」に丸めていた**。
#
# ⚠️ 権限不足・API 無効は ERROR のまま残す。ここに広い文言（"not found" 等）を
# 足すと、資格情報エラーまで「残留ゼロ」に化けて撤収の失敗を見逃す。
# **実際に観測したエラーコードだけ**を足すこと（推測で広げない）。
_PARENT_ABSENT_CODES = (
    "resourcegroupnotfound",  # Azure: RG ごと削除済み
    "parentresourcenotfound",  # Azure: workspace ごと削除済み
)


def _parent_absent(exc: Exception) -> bool:
    """親リソースが消えているか（＝配下の残留はゼロと断定できる）。"""
    text = str(exc).casefold()
    return any(code in text for code in _PARENT_ABSENT_CODES)


def _gcp_scope() -> dict[str, str]:
    """project / region を解決する（terraform outputs > 環境変数）。

    環境変数を先に見ない理由: `GOOGLE_CLOUD_REGION` は他プロジェクト用の値が
    入っていることがあり（実測: asia-northeast1）、本ラボの us-central1 と食い違う。
    """
    import os

    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    region = ""
    try:
        with open(OUTPUTS_PATH, encoding="utf-8") as f:
            outputs = json.load(f)
        project = outputs.get("project_id", {}).get("value") or project
        region = outputs.get("region", {}).get("value") or region
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return {"project": project, "region": region or "us-central1"}


@dataclass(frozen=True)
class Finding:
    """残留1件。`kind` で Tier A / Tier B の種別差を残す。"""

    platform: str
    kind: str
    severity: str
    items: tuple[str, ...] = ()
    note: str | None = None

    def as_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["items"] = list(self.items)
        return record


@dataclass
class CheckResult:
    findings: list[Finding] = field(default_factory=list)

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.severity in {SEVERITY_FAIL, SEVERITY_ERROR}]

    def exit_code(self) -> int:
        return 1 if self.blocking else 0


def lazy_clients(
    clients: dict[str, Any] | None, factory: Callable[[], dict[str, Any]]
) -> Callable[[str], Any]:
    """クライアント解決を **guarded() の内側**へ遅らせるアクセサを返す。

    生成そのものが失敗する場合（SDK 未インストール・認証不備・API 無効）に、
    `guarded` の外で呼んでいると traceback で落ち、
    **「検査できなかった」という結果が1行も残らない**。
    それは「残留ゼロ」と区別が付かず、本モジュールの目的（ERROR も結果として
    可視化する）を裏切る。実際 2026-08-01 に Vertex の検査が
    `ImportError: artifactregistry_v1` で丸ごと落ちた。

    解決は1回だけ行い、以降は使い回す（各 kind で作り直さない）。
    """
    cache: dict[str, Any] = {}

    def get(name: str) -> Any:
        if not cache:
            cache.update(clients if clients is not None else factory())
        return cache[name]

    return get


def guarded(platform: str, kind: str, severity: str) -> Callable[..., Any]:
    """列挙の失敗を ERROR finding に落とすデコレータ。

    API 無効・権限不足で例外が飛ぶと「残留ゼロ」に見えてしまう。
    検査できなかったことを**残留と同じ土俵で可視化する**（移植元の設計）。
    """

    def decorate(fn: Callable[..., Iterable[str]]) -> Callable[..., list[Finding]]:
        def wrapper(*args: Any, **kwargs: Any) -> list[Finding]:
            try:
                items = tuple(fn(*args, **kwargs))
            except Exception as exc:  # noqa: BLE001 - 検査不能も結果の一部
                if _parent_absent(exc):
                    # 親ごと消えている = 配下の残留はゼロ。完全撤収の後に
                    # 検査を回したときに ERROR を出さない（それは「不明」ではない）。
                    return []
                return [
                    Finding(
                        platform=platform,
                        kind=kind,
                        severity=SEVERITY_ERROR,
                        note=f"{type(exc).__name__}: {exc}",
                    )
                ]
            if not items:
                return []
            return [Finding(platform=platform, kind=kind, severity=severity, items=items)]

        return wrapper

    return decorate


# --- Vertex AI ------------------------------------------------------------


def check_vertex(clients: dict[str, Any] | None = None) -> list[Finding]:
    """Vertex Endpoints / GCS / Artifact Registry を列挙する。

    Endpoint は**残ると課金が続き、terraform destroy も HTTP 400 で落ちる**ので FAIL。
    バケットとリポジトリは中身が残っている場合のみ WARN（空なら実害が小さい）。

    **本ラボが作ったものだけを数える**（`LAB_NAME_PREFIX` で絞る）。
    プロジェクトは他用途と共有なので、全バケットを列挙すると他プロジェクトの
    バケットが「Vertex の残留」として比較表に載る（2026-08-01 に実際に 12 件
    誤検出）。残留比較は本ラボの主要な比較軸なので、ここが濁ると成果物が嘘になる。
    """
    client = lazy_clients(clients, _default_gcp_clients)
    scope = _gcp_scope()

    @guarded("vertex", "vertex_endpoint", SEVERITY_FAIL)
    def endpoints() -> Iterable[str]:
        return [
            e.resource_name
            for e in client("aiplatform").Endpoint.list()
            if _is_lab_resource(getattr(e, "display_name", "") or e.resource_name)
        ]

    @guarded("vertex", "gcs_object", SEVERITY_WARN)
    def buckets() -> Iterable[str]:
        storage = client("storage")
        found = []
        for bucket in storage.list_buckets():
            if not _is_lab_resource(bucket.name):
                continue
            if list(storage.list_blobs(bucket.name, max_results=1)):
                found.append(f"gs://{bucket.name}")
        return found

    @guarded("vertex", "artifact_registry", SEVERITY_WARN)
    def repositories() -> Iterable[str]:
        # parent（projects/<p>/locations/<l>）が要る。省くと API は
        # RESOURCE_PROJECT_INVALID を返す（= 検査が一度も成立しない）
        parent = f"projects/{scope['project']}/locations/{scope['region']}"
        listed = client("artifact_registry").list_repositories(request={"parent": parent})
        return [r.name for r in listed if _is_lab_resource(r.name)]

    @guarded("vertex", "registered_model", SEVERITY_WARN)
    def models() -> Iterable[str]:
        """Model Registry の登録モデル。**SDK が作るので terraform destroy では消えない。**

        adapter の teardown も Endpoint しか消さない（設計どおり）。
        つまり Vertex の残留はここに出る。検査項目から漏らすと
        「残留ゼロ」という**嘘の結果**になる（2026-08-01 の実測で1件残っていた）。
        Azure ML 側には同じ検査が最初からあった。
        """
        return [
            f"{m.display_name}:{getattr(m, 'version_id', '?')}"
            for m in client("aiplatform").Model.list()
            if _is_lab_resource(m.display_name)
        ]

    return [*endpoints(), *buckets(), *repositories(), *models()]


# --- SageMaker ------------------------------------------------------------


def check_sagemaker(clients: dict[str, Any] | None = None) -> list[Finding]:
    """Endpoint / S3 / ECR / Model Package Group を列挙する。

    Model Package Group は adapter が teardown で消さない設計なので、
    **想定内の残留**として WARN（消し忘れではないことを note に残す）。

    **本ラボが作ったものだけを数える**（`LAB_NAME_PREFIX` で絞る）。
    AWS アカウントは他用途と共有なので、絞らないと無関係な Endpoint が
    FAIL（= 嘘の赤）に、無関係なバケットが「SageMaker の残留」に載る
    （Vertex 側は同じ絞り込みが無くバケット 12 件を誤検出した。2026-08-01）。

    ECR は Vertex の `artifact_registry` と対になる項目。**列挙しないと
    「Vertex にはレジストリの残留行があるが SageMaker には無い」という
    見かけの差**が出て、Tier A 同士の残留比較が成立しない。
    """
    client = lazy_clients(clients, _default_aws_clients)

    @guarded("sagemaker", "sagemaker_endpoint", SEVERITY_FAIL)
    def endpoints() -> Iterable[str]:
        return [
            e["EndpointName"]
            for e in client("sagemaker").list_endpoints().get("Endpoints", [])
            if _is_lab_resource(e["EndpointName"])
        ]

    @guarded("sagemaker", "sagemaker_endpoint_config", SEVERITY_WARN)
    def endpoint_configs() -> Iterable[str]:
        response = client("sagemaker").list_endpoint_configs()
        return [
            c["EndpointConfigName"]
            for c in response.get("EndpointConfigs", [])
            if _is_lab_resource(c["EndpointConfigName"])
        ]

    @guarded("sagemaker", "sagemaker_model", SEVERITY_WARN)
    def models() -> Iterable[str]:
        """SDK が作る Model。**terraform destroy でも adapter の teardown でも消えない。**

        teardown は同一プロセスで作った Model / EndpointConfig しか消せない
        （名前をインスタンス変数に持つ設計）。別プロセスで teardown すると
        Endpoint だけ消えて Model は残る。検査から漏らすと「残留ゼロ」という
        嘘の結果になる（Vertex の registered_model と同じ落とし穴。2026-08-01）。
        """
        response = client("sagemaker").list_models()
        return [
            m["ModelName"] for m in response.get("Models", []) if _is_lab_resource(m["ModelName"])
        ]

    @guarded("sagemaker", "model_package_group", SEVERITY_WARN)
    def model_packages() -> Iterable[str]:
        response = client("sagemaker").list_model_package_groups()
        return [
            g["ModelPackageGroupName"]
            for g in response.get("ModelPackageGroupSummaryList", [])
            if _is_lab_resource(g["ModelPackageGroupName"])
        ]

    @guarded("sagemaker", "ecr_repository", SEVERITY_WARN)
    def repositories() -> Iterable[str]:
        response = client("ecr").describe_repositories()
        return [
            r["repositoryName"]
            for r in response.get("repositories", [])
            if _is_lab_resource(r["repositoryName"])
        ]

    @guarded("sagemaker", "s3_object", SEVERITY_WARN)
    def buckets() -> Iterable[str]:
        found = []
        for bucket in client("s3").list_buckets().get("Buckets", []):
            if not _is_lab_resource(bucket["Name"]):
                continue
            listing = client("s3").list_objects_v2(Bucket=bucket["Name"], MaxKeys=1)
            if listing.get("KeyCount"):
                found.append(f"s3://{bucket['Name']}")
        return found

    return [
        *endpoints(),
        *endpoint_configs(),
        *models(),
        *model_packages(),
        *repositories(),
        *buckets(),
    ]


# --- Azure ML -------------------------------------------------------------


def check_azureml(clients: dict[str, Any] | None = None) -> list[Finding]:
    """Managed Online Endpoint / Key Vault の論理削除 / Model を列挙する。

    **Key Vault は destroy 後も論理削除で残る**（purge するまで同名再作成が失敗する）。
    Azure 固有の残留なので kind を分けて記録する。
    """
    client = lazy_clients(clients, _default_azure_clients)

    @guarded("azureml", "online_endpoint", SEVERITY_FAIL)
    def endpoints() -> Iterable[str]:
        return [e.name for e in client("ml").online_endpoints.list()]

    @guarded("azureml", "key_vault_soft_deleted", SEVERITY_FAIL)
    def vaults() -> Iterable[str]:
        return [v.name for v in client("keyvault").vaults.list_deleted()]

    @guarded("azureml", "registered_model", SEVERITY_WARN)
    def models() -> Iterable[str]:
        return [f"{m.name}:{getattr(m, 'version', '?')}" for m in client("ml").models.list()]

    return [*endpoints(), *vaults(), *models()]


# --- Databricks -----------------------------------------------------------


def check_databricks(clients: dict[str, Any] | None = None) -> list[Finding]:
    """Serving Endpoint / UC カタログ内オブジェクト / Volume 上の成果物を列挙する。

    Tier B の残留は Tier A と質が違う（データとガバナンス面に残る）。
    """
    client = lazy_clients(clients, _default_databricks_clients)

    @guarded("databricks", "serving_endpoint", SEVERITY_FAIL)
    def endpoints() -> Iterable[str]:
        return [
            e.name for e in client("workspace").serving_endpoints.list() if _is_lab_resource(e.name)
        ]

    @guarded("databricks", "uc_registered_model", SEVERITY_WARN)
    def models() -> Iterable[str]:
        return [
            m.full_name
            for m in client("workspace").registered_models.list()
            if _is_lab_resource(m.full_name)
        ]

    @guarded("databricks", "uc_volume", SEVERITY_WARN)
    def volumes() -> Iterable[str]:
        """Volume は **catalog / schema を指定しないと列挙できない**。

        以前は `catalog_name="" , schema_name=""` を渡していた（テストは注入
        クライアントで通るが、実 API は空文字を受理しない）。ERROR 止まりなら
        まだしも、実クラウドで一度も成立しない検査は「残留ゼロ」と誤読される。
        スコープは terraform outputs から解決する（GCP / Azure と同じ方針）。
        """
        scope = _databricks_scope()
        if not scope["catalog"] or not scope["schema"]:
            raise RuntimeError(
                f"catalog / schema を解決できない（{DATABRICKS_OUTPUTS_PATH} が無い）。"
                " apply 直後に outputs を保存すること"
            )
        try:
            listed = client("workspace").volumes.list(
                catalog_name=scope["catalog"], schema_name=scope["schema"]
            )
            return [v.full_name for v in listed]
        except Exception as exc:  # noqa: BLE001 - 「もう無い」と「見られない」を分ける
            if _looks_missing(exc):
                return []  # カタログごと destroy 済み = 残留ゼロ（検査不能ではない）
            raise

    return [*endpoints(), *models(), *volumes()]


# --- Snowflake ------------------------------------------------------------


def check_snowflake(clients: dict[str, Any] | None = None) -> list[Finding]:
    """Stage 成果物 / スキーマ内オブジェクト / Time Travel / Fail-safe を列挙する。

    **Fail-safe（7日）は設定で消せない**ので、必ず1件 WARN として出す。
    「残留ゼロ」と誤読させないための固定行であり、Tier A と同じ土俵で数えない。
    """
    client = lazy_clients(clients, _default_snowflake_clients)

    @guarded("snowflake", "warehouse_running", SEVERITY_FAIL)
    def warehouses() -> Iterable[str]:
        rows = client("session").sql("SHOW WAREHOUSES").collect()
        return [
            _row_value(r, "name")
            for r in rows
            if _row_value(r, "state") == "STARTED" and _is_lab_row(r)
        ]

    @guarded("snowflake", "schema_object", SEVERITY_WARN)
    def models() -> Iterable[str]:
        rows = client("session").sql("SHOW MODELS").collect()
        return [_row_value(r, "name") for r in rows if _is_lab_row(r)]

    @guarded("snowflake", "stage_file", SEVERITY_WARN)
    def stage_files() -> Iterable[str]:
        rows = client("session").sql("SHOW STAGES").collect()
        return [_row_value(r, "name") for r in rows if _is_lab_row(r)]

    findings = [*warehouses(), *models(), *stage_files()]
    findings.append(
        Finding(
            platform="snowflake",
            kind="fail_safe",
            severity=SEVERITY_WARN,
            items=("7 days",),
            note="Fail-safe は設定で消せない（Tier A と同じ土俵で数えない）",
        )
    )
    return findings


CHECKS: dict[str, Callable[..., list[Finding]]] = {
    "vertex": check_vertex,
    "sagemaker": check_sagemaker,
    "azureml": check_azureml,
    "databricks": check_databricks,
    "snowflake": check_snowflake,
}


# --- クライアント解決（遅延 import）--------------------------------------


def _default_gcp_clients() -> dict[str, Any]:
    # google.cloud は名前空間パッケージで、mypy は `storage` を属性として解決できない
    # （実行時は google-cloud-storage が提供する）。実害が無いのでこの1行だけ無視する。
    from google.cloud import aiplatform, artifactregistry_v1, storage  # type: ignore[attr-defined]

    scope = _gcp_scope()
    # **init を必ず呼ぶ。** 呼ばないと Endpoint.list() / Model.list() は
    # 環境変数（GOOGLE_CLOUD_REGION 等）由来の**別リージョン**を見に行き、
    # 対象リージョンに残っていても「残留ゼロ」と report する
    # （2026-08-01 実測: 本ラボは us-central1 だが env は asia-northeast1 を指しており、
    #   無関係なプロジェクトのモデルを列挙していた = 嘘の緑）。
    aiplatform.init(project=scope["project"], location=scope["region"])

    return {
        "aiplatform": aiplatform,
        "storage": storage.Client(project=scope["project"] or None),
        "artifact_registry": artifactregistry_v1.ArtifactRegistryClient(),
    }


def _default_aws_clients() -> dict[str, Any]:
    import boto3

    return {
        "sagemaker": boto3.client("sagemaker"),
        "s3": boto3.client("s3"),
        "ecr": boto3.client("ecr"),
    }


def _default_azure_clients() -> dict[str, Any]:
    """Azure ML の残留検査に要る2クライアント。

    `MLClient` は Workspace スコープ（Endpoint / Model を見る）、
    `KeyVaultManagementClient` は **サブスクリプションスコープ**（論理削除された
    Key Vault を見る）。Key Vault の論理削除は Workspace を消しても残り、
    同名での再 apply をブロックするため、別クライアントで見に行く必要がある。

    project/region 相当（subscription / resource group / workspace）は
    terraform outputs から解決する（GCP と同じ方針）。
    """
    import os

    from azure.ai.ml import MLClient
    from azure.identity import DefaultAzureCredential
    from azure.mgmt.keyvault import KeyVaultManagementClient

    scope = _azure_scope()
    subscription = scope["subscription_id"] or os.environ.get("AZURE_SUBSCRIPTION_ID", "")
    credential = DefaultAzureCredential()

    return {
        "ml": MLClient(
            credential=credential,
            subscription_id=subscription,
            resource_group_name=scope["resource_group"],
            workspace_name=scope["workspace"],
        ),
        "keyvault": KeyVaultManagementClient(credential, subscription),
    }


def _azure_scope() -> dict[str, str]:
    """subscription / resource group / workspace を terraform outputs から解決する。"""
    values = {"subscription_id": "", "resource_group": "", "workspace": ""}
    try:
        with open(AZURE_OUTPUTS_PATH, encoding="utf-8") as f:
            outputs = json.load(f)
        values["subscription_id"] = outputs.get("subscription_id", {}).get("value") or ""
        values["resource_group"] = outputs.get("resource_group_name", {}).get("value") or ""
        values["workspace"] = outputs.get("workspace_name", {}).get("value") or ""
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return values


def _databricks_scope() -> dict[str, str]:
    """catalog / schema を terraform outputs から解決する（Volume 列挙に必須）。"""
    values = {"catalog": "", "schema": ""}
    try:
        with open(DATABRICKS_OUTPUTS_PATH, encoding="utf-8") as f:
            outputs = json.load(f)
        values["catalog"] = outputs.get("catalog_name", {}).get("value") or ""
        values["schema"] = outputs.get("schema_name", {}).get("value") or ""
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return values


def _default_databricks_clients() -> dict[str, Any]:
    from databricks.sdk import WorkspaceClient

    return {"workspace": WorkspaceClient()}


def _default_snowflake_clients() -> dict[str, Any]:
    """Snowpark Session。**`getOrCreate()` は使わない。**

    `getOrCreate()` は connections.toml の既定接続を探すため、環境変数だけで
    運用している本ラボでは `Default connection with name 'default' cannot be found`
    になり、検査が丸ごと ERROR になる（2026-08-01 実測）。
    adapter と同じ `connection_parameters()`（env + terraform outputs）で組む。

    ただし **ロールは名乗らない**（`role=""`）。残留検査は destroy の**後**に走るのに、
    adapter が使う `MCML_DEV_ROLE` は destroy で消える。そのロールを指定すると
    `Role 'MCML_DEV_ROLE' does not exist or not authorized` で接続自体が落ち、
    「検査できなかった」しか残らない（2026-08-01 実測）。
    **撤退で消える権限に、撤退後の検査を依存させない。**
    """
    import dataclasses

    from snowflake.snowpark import Session

    from platforms.shared.config import load_settings
    from platforms.snowflake.adapter import connection_parameters

    config = dataclasses.replace(load_settings().snowflake(), role="")
    return {"session": Session.builder.configs(connection_parameters(config)).create()}


def _is_lab_row(row: Any) -> bool:
    """SHOW 系の1行が本ラボの資産か。

    **名前だけでは足りない。** stage 名は `CODE`、モデル名は `CALIFORNIA_HOUSING` で
    prefix を含まず、所属 database（`MCML_DEV`）まで見て初めて本ラボと分かる。
    絞らないとアカウント共有の他資産が残留として比較表に載る
    （2026-08-01 実測: 無関係な stage `BLOBS` を Snowflake の残留1件として計上した）。
    """
    return any(_is_lab_resource(_row_value(row, key)) for key in ("name", "database_name"))


def _row_value(row: Any, key: str) -> str:
    """SHOW 系の戻り（Row / dict / tuple）から列を取り出す。"""
    if isinstance(row, dict):
        return str(row.get(key, ""))
    as_dict = getattr(row, "as_dict", None)
    if callable(as_dict):
        return str(as_dict().get(key, ""))
    return str(getattr(row, key, ""))


# --- 実行 -----------------------------------------------------------------


def run_checks(
    platforms: Iterable[str], clients: dict[str, dict[str, Any]] | None = None
) -> CheckResult:
    """指定基盤の検査をまとめて回す。1基盤が落ちても他は続ける。"""
    clients = clients or {}
    result = CheckResult()
    for platform in platforms:
        check = CHECKS[platform]
        result.findings.extend(check(clients.get(platform)))
    return result


def format_report(result: CheckResult) -> str:
    lines = []
    for finding in result.findings:
        items = ", ".join(finding.items) if finding.items else "-"
        note = f"  # {finding.note}" if finding.note else ""
        lines.append(f"[{finding.severity}] {finding.platform}/{finding.kind}: {items}{note}")
    if not lines:
        lines.append("残留なし（検査した範囲では）")
    blocking = len(result.blocking)
    lines.append(f"-- FAIL/ERROR {blocking} 件 / 全 {len(result.findings)} 件")
    return "\n".join(lines)


def record_infra_event(result: CheckResult, platforms: Iterable[str] | None = None) -> None:
    """infra_events へ記録する。到達不能でも検査結果は標準出力に残す。

    **検査した基盤は findings が0件でも1行残す。** finding のある基盤だけ書くと
    「検査して残留ゼロ」と「そもそも検査していない」が DB 上で区別できず、
    残留比較表の空欄の意味が決まらない（撤退できた証拠が消える）。
    """
    import uuid

    from core.telemetry.schemas import InfraAction, InfraEvent, Platform, Status
    from platforms.neon.run_sink import NeonRunSink

    sink = NeonRunSink()
    targets = set(platforms) if platforms is not None else {f.platform for f in result.findings}
    for platform in sorted(targets):
        findings = [f.as_record() for f in result.findings if f.platform == platform]
        sink.record_infra_event(
            InfraEvent(
                # event_id は uuid 列。同じ検査を2回流しても別行として残す（時系列で見る）
                event_id=str(uuid.uuid4()),
                platform=Platform(platform),
                action=InfraAction.DESTROY,
                status=Status.FAILURE
                if any(f["severity"] == "FAIL" for f in findings)
                else Status.SUCCESS,
                residual_resources={"findings": findings},
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_residual")
    parser.add_argument("--platform", choices=PLATFORMS)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--record", action="store_true", help="infra_events へ記録する")
    parser.add_argument("--json", action="store_true", help="機械可読で出す")
    args = parser.parse_args(argv)

    if not args.all and not args.platform:
        parser.error("--platform か --all のどちらかが必要")

    targets = PLATFORMS if args.all else (args.platform,)
    result = run_checks(targets)

    if args.json:
        print(json.dumps([f.as_record() for f in result.findings], ensure_ascii=False, indent=2))
    else:
        print(format_report(result))

    if args.record:
        try:
            record_infra_event(result, targets)
        except Exception as exc:  # noqa: BLE001 - 記録失敗で検査結果を捨てない
            print(f"infra_events への記録に失敗: {exc}", file=sys.stderr)

    return result.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
