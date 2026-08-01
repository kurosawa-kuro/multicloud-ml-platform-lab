"""残留リソース検査（scripts/check_residual.py）の検証。

**実クラウドを叩かない。** クライアントを注入して「分類と exit code」を固定する。
ここが濁ると比較レポートの残留列が嘘になる。
"""

from __future__ import annotations

from typing import Any

import pytest
from tests.conftest import load_script

residual = load_script("check_residual")


class FakeEndpoint:
    def __init__(self, name: str) -> None:
        self.resource_name = name
        self.name = name


class FakeModel:
    def __init__(self, display_name: str, version_id: str = "1") -> None:
        self.display_name = display_name
        self.version_id = version_id


class FakeAiplatform:
    class Endpoint:
        listed: list[FakeEndpoint] = []

        @classmethod
        def list(cls) -> list[FakeEndpoint]:
            return cls.listed

    class Model:
        listed: list[FakeModel] = []

        @classmethod
        def list(cls) -> list[FakeModel]:
            return cls.listed


class FakeBucket:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeStorage:
    def __init__(self, buckets: dict[str, int]) -> None:
        self._buckets = buckets

    def list_buckets(self) -> list[FakeBucket]:
        return [FakeBucket(name) for name in self._buckets]

    def list_blobs(self, bucket: str, max_results: int = 1) -> list[str]:
        return ["blob"] * min(self._buckets[bucket], max_results)


class FakeArtifactRegistry:
    def __init__(self, repos: list[str]) -> None:
        self._repos = repos
        self.requests: list[dict[str, str]] = []

    def list_repositories(self, request: dict[str, str] | None = None) -> list[Any]:
        # parent 必須（省くと実 API は RESOURCE_PROJECT_INVALID を返す）
        self.requests.append(request or {})
        return [type("Repo", (), {"name": r})() for r in self._repos]


def gcp_clients(
    endpoints: list[str],
    buckets: dict[str, int],
    repos: list[str],
    models: list[str] | None = None,
) -> dict[str, Any]:
    FakeAiplatform.Endpoint.listed = [FakeEndpoint(e) for e in endpoints]
    FakeAiplatform.Model.listed = [FakeModel(m) for m in (models or [])]
    return {
        "aiplatform": FakeAiplatform,
        "storage": FakeStorage(buckets),
        "artifact_registry": FakeArtifactRegistry(repos),
    }


def test_endpoint_left_behind_is_fail_and_exits_nonzero() -> None:
    """課金が続くものは FAIL。exit 1 にしないと撤退漏れが素通りする。"""
    result = residual.run_checks(
        ["vertex"],
        clients={"vertex": gcp_clients(["projects/p/endpoints/mcml-dev-endpoint"], {}, [])},
    )

    assert [f.severity for f in result.findings] == ["FAIL"]
    assert result.exit_code() == 1


def test_empty_bucket_is_not_reported() -> None:
    """空バケットは残留として数えない（実害が無いものを並べると表が読めなくなる）。"""
    result = residual.run_checks(
        ["vertex"], clients={"vertex": gcp_clients([], {"example-gcp-project-mcml-dev": 0}, [])}
    )

    assert result.findings == []
    assert result.exit_code() == 0


def test_non_empty_bucket_is_warn_only() -> None:
    result = residual.run_checks(
        ["vertex"], clients={"vertex": gcp_clients([], {"example-gcp-project-mcml-dev": 3}, [])}
    )

    assert [f.severity for f in result.findings] == ["WARN"]
    assert result.exit_code() == 0


def test_unverifiable_check_becomes_error_not_silence() -> None:
    """API 無効・権限不足は「残留ゼロ」ではなく ERROR。黙ると嘘の緑になる。"""

    class Broken:
        class Endpoint:
            @staticmethod
            def list() -> list[Any]:
                raise PermissionError("Permission denied on aiplatform.googleapis.com")

    result = residual.run_checks(
        ["vertex"],
        clients={
            "vertex": {
                "aiplatform": Broken,
                "storage": FakeStorage({}),
                "artifact_registry": FakeArtifactRegistry([]),
            }
        },
    )

    # aiplatform を使う検査は endpoint / registered_model の2つ。**両方 ERROR で残る**
    # （片方でも黙ると「残留ゼロ」と読めてしまう）
    assert [f.severity for f in result.findings] == ["ERROR", "ERROR"]
    assert {f.kind for f in result.findings} == {"vertex_endpoint", "registered_model"}
    assert result.exit_code() == 1


def test_snowflake_always_reports_fail_safe() -> None:
    """Fail-safe は設定で消せない。**必ず1件出す**ことで「残留ゼロ」の誤読を防ぐ。"""

    class FakeSession:
        def sql(self, statement: str) -> Any:
            return type("R", (), {"collect": staticmethod(lambda: [])})()

    result = residual.run_checks(["snowflake"], clients={"snowflake": {"session": FakeSession()}})

    kinds = {f.kind for f in result.findings}
    assert "fail_safe" in kinds
    assert result.exit_code() == 0  # 消せない残留は FAIL にしない（撤退失敗ではない）


def test_report_lists_blocking_count() -> None:
    result = residual.run_checks(
        ["vertex"],
        clients={"vertex": gcp_clients(["projects/p/endpoints/mcml-dev-endpoint"], {}, [])},
    )
    report = residual.format_report(result)

    assert "FAIL/ERROR 1 件" in report


# --- クライアント生成の失敗も「結果」として残す ---------------------------


def test_client_construction_failure_becomes_error_findings() -> None:
    """SDK 未インストール等で**クライアントを作れない**場合も ERROR で残ること。

    生成を guarded() の外でやっていると traceback で丸ごと落ち、
    「検査できなかった」という結果が1行も残らない（= 残留ゼロと区別が付かない）。
    2026-08-01 に Vertex の検査が ImportError で落ちた回帰の防止。
    """

    def exploding() -> dict[str, object]:
        raise ImportError("cannot import name 'artifactregistry_v1' from 'google.cloud'")

    accessor = residual.lazy_clients(None, exploding)

    @residual.guarded("vertex", "vertex_endpoint", residual.SEVERITY_FAIL)
    def endpoints():
        return [e for e in accessor("aiplatform").Endpoint.list()]

    findings = endpoints()

    assert len(findings) == 1
    assert findings[0].severity == residual.SEVERITY_ERROR
    assert "ImportError" in (findings[0].note or "")


def test_injected_clients_are_used_without_calling_the_factory() -> None:
    """注入があれば既定ファクトリを呼ばない（テストが実クラウドを触らない担保）。"""

    def must_not_run() -> dict[str, object]:
        raise AssertionError("既定クライアントが構築された")

    accessor = residual.lazy_clients({"aiplatform": "injected"}, must_not_run)

    assert accessor("aiplatform") == "injected"


def test_every_platform_check_survives_a_broken_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """5基盤とも「検査不能」を ERROR finding にして返すこと（例外を投げない）。"""

    def exploding() -> dict[str, object]:
        raise RuntimeError("SDK unavailable")

    for name in (
        "_default_gcp_clients",
        "_default_aws_clients",
        "_default_azure_clients",
        "_default_databricks_clients",
        "_default_snowflake_clients",
    ):
        monkeypatch.setattr(residual, name, exploding)

    result = residual.run_checks(residual.PLATFORMS)

    assert result.exit_code() == 1
    assert {f.platform for f in result.findings} >= set(residual.PLATFORMS)
    assert all(
        f.severity == residual.SEVERITY_ERROR
        for f in result.findings
        if f.kind != "fail_safe"  # Snowflake の固定 WARN 行だけは常に出る
    )


def test_clean_platform_still_records_a_row() -> None:
    """残留ゼロでも「検査した」行を残すこと。

    finding のある基盤だけ書くと、比較表の空欄が「撤退できた」なのか
    「検査していない」なのか決まらない（撤退できた証拠が消える）。
    """
    recorded: list[Any] = []

    class SpySink:
        def record_infra_event(self, event: Any) -> int:
            recorded.append(event)
            return 1

    import sys
    import types

    module = types.ModuleType("platforms.neon.run_sink")
    module.NeonRunSink = SpySink  # type: ignore[attr-defined]
    sys.modules["platforms.neon.run_sink"] = module
    try:
        residual.record_infra_event(residual.CheckResult(), ["vertex"])
    finally:
        del sys.modules["platforms.neon.run_sink"]

    assert len(recorded) == 1
    assert recorded[0].platform.value == "vertex"
    assert recorded[0].residual_resources == {"findings": []}
    assert recorded[0].status.value == "success"


# --- 本ラボのリソースだけを数える -----------------------------------------


def test_other_projects_resources_are_not_counted_as_residue() -> None:
    """共有プロジェクトの無関係なバケットを「Vertex の残留」にしない。

    2026-08-01 の実測で、他プロジェクトのバケット 12 件が残留として
    比較表に載る誤検出が出た（残留比較は本ラボの主要な比較軸）。
    """
    result = residual.run_checks(
        ["vertex"],
        clients={
            "vertex": gcp_clients(
                ["projects/p/endpoints/unrelated-service"],
                {"example-gcp-project-news-poc": 5, "example-gcp-project_cloudbuild": 9},
                ["other-team-repo"],
            )
        },
    )

    assert result.findings == []
    assert result.exit_code() == 0


def test_artifact_registry_is_queried_with_a_parent() -> None:
    """parent を渡すこと。省くと実 API は 400 を返し検査が一度も成立しない。"""
    registry = FakeArtifactRegistry(["mcml"])
    clients = gcp_clients([], {}, [])
    clients["artifact_registry"] = registry

    residual.run_checks(["vertex"], clients={"vertex": clients})

    assert registry.requests, "list_repositories が呼ばれていない"
    parent = registry.requests[0].get("parent", "")
    assert parent.startswith("projects/") and "/locations/" in parent


def test_registered_model_is_reported_as_residue() -> None:
    """SDK が作った Model は terraform destroy でも teardown でも消えない。

    検査項目から漏らすと「残留ゼロ」という嘘の結果になる
    （2026-08-01 の実測で 1 件残っていたのを後から発見した回帰の防止）。
    """
    result = residual.run_checks(
        ["vertex"],
        clients={"vertex": gcp_clients([], {}, [], models=["mcml-california-housing"])},
    )

    kinds = {f.kind for f in result.findings}
    assert "registered_model" in kinds
    assert result.exit_code() == 0  # 課金は続かないので WARN 止まり


def test_other_teams_models_are_not_counted() -> None:
    result = residual.run_checks(
        ["vertex"], clients={"vertex": gcp_clients([], {}, [], models=["kaggle-playground"])}
    )

    assert result.findings == []


# --- Tier B: 絞り込みとスコープ -------------------------------------------


class FakeSnowflakeSession:
    """SHOW 系の戻りを差し替えるだけの最小セッション。"""

    def __init__(self, rows: dict[str, list[dict[str, str]]]) -> None:
        self._rows = rows

    def sql(self, statement: str) -> Any:
        rows = self._rows.get(statement, [])
        return type("R", (), {"collect": staticmethod(lambda: rows)})()


def test_snowflake_counts_only_lab_objects() -> None:
    """アカウント共有の他資産を Snowflake の残留に混ぜない。

    stage 名（`CODE`）にはラボ prefix が入らないため、所属 database で判定する。
    2026-08-01 の実測では無関係な stage `BLOBS` を残留 1 件として計上していた。
    """
    session = FakeSnowflakeSession(
        {
            "SHOW STAGES": [
                {"name": "CODE", "database_name": "MCML_DEV"},
                {"name": "BLOBS", "database_name": "SNOWFLAKE_LEARNING_DB"},
            ],
            "SHOW MODELS": [{"name": "OTHER_MODEL", "database_name": "PLAYGROUND"}],
            "SHOW WAREHOUSES": [
                {"name": "COMPUTE_WH", "state": "STARTED", "database_name": ""},
                {"name": "MCML_DEV_WH", "state": "SUSPENDED", "database_name": ""},
            ],
        }
    )

    result = residual.run_checks(["snowflake"], clients={"snowflake": {"session": session}})

    stages = [f for f in result.findings if f.kind == "stage_file"]
    assert [f.items for f in stages] == [("CODE",)]
    assert not [f for f in result.findings if f.kind == "schema_object"]
    # 他アカウント資産の稼働中 warehouse を FAIL にしない（撤退失敗ではない）
    assert not [f for f in result.findings if f.kind == "warehouse_running"]
    assert result.exit_code() == 0


def test_snowflake_uppercase_names_match_the_lab_prefix() -> None:
    """識別子は大文字化されて作られる。大小文字を区別すると絞り込みが無効化される。"""
    assert residual._is_lab_resource("MCML_DEV")
    assert residual._is_lab_resource("mcml-dev-endpoint")
    assert not residual._is_lab_resource("SNOWFLAKE_LEARNING_DB")


# --- SageMaker ------------------------------------------------------------


class FakeSageMaker:
    def __init__(
        self,
        endpoints: list[str],
        configs: list[str],
        model_package_groups: list[str],
        models: list[str] | None = None,
    ) -> None:
        self._endpoints = endpoints
        self._configs = configs
        self._groups = model_package_groups
        self._models = models or []

    def list_models(self) -> dict[str, Any]:
        return {"Models": [{"ModelName": n} for n in self._models]}

    def list_endpoints(self) -> dict[str, Any]:
        return {"Endpoints": [{"EndpointName": n} for n in self._endpoints]}

    def list_endpoint_configs(self) -> dict[str, Any]:
        return {"EndpointConfigs": [{"EndpointConfigName": n} for n in self._configs]}

    def list_model_package_groups(self) -> dict[str, Any]:
        summaries = [{"ModelPackageGroupName": n} for n in self._groups]
        return {"ModelPackageGroupSummaryList": summaries}


class FakeS3:
    def __init__(self, buckets: dict[str, int]) -> None:
        self._buckets = buckets

    def list_buckets(self) -> dict[str, Any]:
        return {"Buckets": [{"Name": name} for name in self._buckets]}

    def list_objects_v2(self, Bucket: str, MaxKeys: int = 1) -> dict[str, Any]:  # noqa: N803
        return {"KeyCount": min(self._buckets[Bucket], MaxKeys)}


class FakeEcr:
    def __init__(self, repositories: list[str]) -> None:
        self._repositories = repositories

    def describe_repositories(self) -> dict[str, Any]:
        return {"repositories": [{"repositoryName": n} for n in self._repositories]}


def aws_clients(
    endpoints: list[str],
    buckets: dict[str, int],
    repositories: list[str],
    configs: list[str] | None = None,
    model_package_groups: list[str] | None = None,
    models: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "sagemaker": FakeSageMaker(endpoints, configs or [], model_package_groups or [], models),
        "s3": FakeS3(buckets),
        "ecr": FakeEcr(repositories),
    }


def test_sagemaker_counts_only_lab_resources() -> None:
    """アカウント共有の他資産を SageMaker の残留に混ぜない。

    絞り込みが無いと、無関係な Endpoint が FAIL（嘘の赤 = 撤退失敗の誤報）に、
    無関係なバケットが「SageMaker の残留」として比較表に載る。
    Vertex 側は同じ穴でバケット 12 件を誤検出した（2026-08-01 実測）。
    """
    result = residual.run_checks(
        ["sagemaker"],
        clients={
            "sagemaker": aws_clients(
                ["other-team-endpoint"],
                {"unrelated-backups": 42},
                ["other-team-repo"],
                configs=["other-team-config"],
                model_package_groups=["other-team-models"],
                models=["other-team-model"],
            )
        },
    )

    assert result.findings == []
    assert result.exit_code() == 0


def test_sagemaker_lab_endpoint_is_fail_and_model_package_group_is_warn() -> None:
    """Endpoint（常時課金）は FAIL、Model Package Group は**想定内の残留**で WARN。"""
    result = residual.run_checks(
        ["sagemaker"],
        clients={
            "sagemaker": aws_clients(
                ["mcml-dev-endpoint"],
                {"mcml-dev-123456789012": 3},
                [],
                model_package_groups=["mcml-dev-models"],
            )
        },
    )

    by_kind = {f.kind: f.severity for f in result.findings}
    assert by_kind["sagemaker_endpoint"] == "FAIL"
    assert by_kind["model_package_group"] == "WARN"
    assert by_kind["s3_object"] == "WARN"
    assert result.exit_code() == 1


def test_sagemaker_model_is_reported_as_residue() -> None:
    """SDK が作った Model は destroy でも teardown でも消えない。

    teardown は同一プロセスで作った名前しか消せないため、別プロセスで叩くと
    Endpoint だけ消えて Model が残る。検査項目から漏らすと「残留ゼロ」の嘘になる。
    """
    result = residual.run_checks(
        ["sagemaker"],
        clients={"sagemaker": aws_clients([], {}, [], models=["mcml-dev-model-3dd6870ff9d8"])},
    )

    models = [f for f in result.findings if f.kind == "sagemaker_model"]
    assert [f.items for f in models] == [("mcml-dev-model-3dd6870ff9d8",)]
    assert result.exit_code() == 0


def test_sagemaker_reports_ecr_repositories() -> None:
    """ECR は Vertex の artifact_registry と対の項目。

    列挙しないと「Vertex にはレジストリの残留行があるが SageMaker には無い」
    という**見かけの差**が出て、Tier A 同士の残留比較が成立しない。
    """
    result = residual.run_checks(
        ["sagemaker"],
        clients={"sagemaker": aws_clients([], {}, ["mcml-dev-training", "mcml-dev-serving"])},
    )

    repos = [f for f in result.findings if f.kind == "ecr_repository"]
    assert [f.items for f in repos] == [("mcml-dev-training", "mcml-dev-serving")]
    assert result.exit_code() == 0


class FakeVolumes:
    def __init__(self, names: list[str], *, error: Exception | None = None) -> None:
        self._names = names
        self._error = error
        self.calls: list[dict[str, str]] = []

    def list(self, catalog_name: str, schema_name: str) -> list[Any]:
        self.calls.append({"catalog_name": catalog_name, "schema_name": schema_name})
        if self._error is not None:
            raise self._error
        return [type("V", (), {"full_name": n})() for n in self._names]


def databricks_client(volumes: FakeVolumes) -> dict[str, Any]:
    empty = type("L", (), {"list": staticmethod(lambda: [])})()
    workspace = type(
        "W",
        (),
        {"serving_endpoints": empty, "registered_models": empty, "volumes": volumes},
    )()
    return {"workspace": workspace}


def test_databricks_volume_listing_uses_the_terraform_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空文字ではなく outputs の catalog / schema で列挙すること。

    実 API は `catalog_name=""` を受理しない。空文字のままだと実クラウドでは
    一度も成立せず、「残留ゼロ」と読める結果しか残らない。
    """
    monkeypatch.setattr(
        residual, "_databricks_scope", lambda: {"catalog": "mcml_dev", "schema": "ml"}
    )
    volumes = FakeVolumes(["mcml_dev.ml.artifacts"])

    result = residual.run_checks(["databricks"], clients={"databricks": databricks_client(volumes)})

    assert volumes.calls == [{"catalog_name": "mcml_dev", "schema_name": "ml"}]
    assert [f.kind for f in result.findings] == ["uc_volume"]
    assert result.exit_code() == 0


def test_databricks_volume_scope_missing_is_an_error_not_silence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """outputs が無ければ「検査できなかった」を残す（黙って 0 件にしない）。"""
    monkeypatch.setattr(residual, "_databricks_scope", lambda: {"catalog": "", "schema": ""})

    result = residual.run_checks(
        ["databricks"], clients={"databricks": databricks_client(FakeVolumes([]))}
    )

    assert [f.severity for f in result.findings] == ["ERROR"]
    assert result.exit_code() == 1


def test_databricks_deleted_catalog_is_zero_residue(monkeypatch: pytest.MonkeyPatch) -> None:
    """destroy 済みでカタログごと消えているのは残留ゼロ。ERROR にしない。"""
    monkeypatch.setattr(
        residual, "_databricks_scope", lambda: {"catalog": "mcml_dev", "schema": "ml"}
    )
    volumes = FakeVolumes([], error=RuntimeError("Catalog 'mcml_dev' does not exist."))

    result = residual.run_checks(["databricks"], clients={"databricks": databricks_client(volumes)})

    assert result.findings == []
    assert result.exit_code() == 0


# --- 親リソース不在（2026-08-01 追加・修正08）-----------------------------
#
# 完全撤収の後に検査を回すと、以前は列挙 API の例外がそのまま ERROR になり、
# 「調べられなかった」と「残留ゼロ」が区別できなかった。
# 親が消えている = 配下はゼロと断定できるので、ERROR にしない。


def test_parent_absent_is_reported_as_no_residual() -> None:
    """RG ごと消えているときに ERROR を出さない（残留ゼロと断定できる）。"""
    check_residual = load_script("check_residual")

    @check_residual.guarded("azureml", "online_endpoint", check_residual.SEVERITY_FAIL)
    def enumerate_gone() -> list[str]:
        raise RuntimeError(
            "ResourceNotFoundError: (ResourceGroupNotFound) "
            "Resource group 'mcml-dev-rg' could not be found."
        )

    assert enumerate_gone() == []


def test_workspace_absent_is_reported_as_no_residual() -> None:
    check_residual = load_script("check_residual")

    @check_residual.guarded("azureml", "registered_model", check_residual.SEVERITY_WARN)
    def enumerate_gone() -> list[str]:
        raise RuntimeError(
            "(ParentResourceNotFound) Failed to perform 'read' on resource(s) of type "
            "'workspaces/models', because the parent resource could not be found."
        )

    assert enumerate_gone() == []


def test_permission_error_is_still_an_error() -> None:
    """**権限不足は ERROR のまま。** ここを緩めると撤収の失敗を見逃す。"""
    check_residual = load_script("check_residual")

    @check_residual.guarded("azureml", "online_endpoint", check_residual.SEVERITY_FAIL)
    def enumerate_denied() -> list[str]:
        raise RuntimeError("AuthorizationFailed: does not have authorization to perform action")

    findings = enumerate_denied()

    assert [f.severity for f in findings] == [check_residual.SEVERITY_ERROR]


def test_api_disabled_is_still_an_error() -> None:
    """API 無効も「調べられなかった」であって「無かった」ではない。"""
    check_residual = load_script("check_residual")

    @check_residual.guarded("vertex", "endpoint", check_residual.SEVERITY_FAIL)
    def enumerate_disabled() -> list[str]:
        raise RuntimeError("403 Cloud AI Platform API has not been used in project")

    assert [f.severity for f in enumerate_disabled()] == [check_residual.SEVERITY_ERROR]


def test_parent_absent_codes_are_observed_not_guessed() -> None:
    """判定を広い文言で行わないこと（"not found" 等を足すと権限エラーを飲む）。"""
    check_residual = load_script("check_residual")

    for code in check_residual._PARENT_ABSENT_CODES:
        assert code.endswith("notfound"), f"親不在の判定に広すぎる文言がある: {code!r}"
