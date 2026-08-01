"""Snowflake adapter の**固有**検証。共通契約は test_adapter_contract.py。

Tier B（Snowflake）でしか成り立たない形:

  - 実行は **DDL（CREATE PROCEDURE）+ CALL** だけ。ジョブ資源が無い
  - sproc の IMPORTS が stage 上のパッケージを指す = 統一単位の担保
  - 登録は artifact URI ではなく **復元したモデル**を log_model
  - **deploy でインフラを作らない**（既定バージョンの切り替えのみ）
  - teardown 後も Fail-safe（7日）が残る = 設定で消せない残留
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.conftest import REPO_ROOT
from tests.fakes import snowflake as fake
from tests.fakes.snowflake import ARTIFACT_URI, MODEL_VERSION, FakeRegistry, FakeSession

from core.telemetry.schemas import Status
from core.telemetry.sinks import JsonlRunSink
from platforms.snowflake.adapter import (
    SPROC_HANDLER,
    SnowflakeConnectionError,
    connection_parameters,
)

CONFIG = fake.config()


def test_schema_level_object_names() -> None:
    """モデルがデータと同じ名前空間に居る（Tier B の特徴）。"""
    assert CONFIG.qualified_model == "MCML_DEV.ML.CALIFORNIA_HOUSING"
    assert CONFIG.qualified_stage == "@MCML_DEV.ML.CODE"
    assert CONFIG.package_stage_path == "@MCML_DEV.ML.CODE/dist/core_ml.zip"


def test_upload_to_stage_disables_auto_compress(sink: JsonlRunSink, tmp_path: Path) -> None:
    """再圧縮されると sproc の IMPORTS が読めない。"""
    adapter, session, _ = fake.build(sink)
    package = tmp_path / "core_ml.zip"
    package.write_bytes(b"zip")

    remote = adapter.upload_to_stage(package)

    assert remote == CONFIG.package_stage_path
    put = session.file.puts[0]
    assert put["auto_compress"] is False
    assert put["overwrite"] is True


def test_submit_training_declares_procedure_then_calls(sink: JsonlRunSink) -> None:
    adapter, session, _ = fake.build(sink)

    run = adapter.submit_training({"num_leaves": 31})

    assert run.status is Status.SUCCESS
    ddl, call = session.statements[0], session.statements[1]
    assert ddl.startswith(
        f"CREATE OR REPLACE PROCEDURE {CONFIG.qualified_procedure}(PARAMS VARIANT)"
    )
    assert f"HANDLER = '{SPROC_HANDLER}'" in ddl
    # IMPORTS が stage 上のパッケージを指す = Tier B の統一単位
    assert f"IMPORTS = ('{CONFIG.package_stage_path}')" in ddl
    assert "'lightgbm>=4.6,<4.7'" in ddl
    payload = json.loads(call.split("PARSE_JSON('")[1].rsplit("'))", 1)[0])
    assert payload["num_leaves"] == 31
    assert payload["source_table"] == CONFIG.source_table
    # ジョブ側（sproc）が ml_runs を書くための識別子
    assert payload["run_id"] == run.run_id
    assert payload["attempt"] == 1
    # メトリクスは sproc の戻りから拾う（Tier A はジョブの成果物から）
    assert run.metrics["rmse"] == pytest.approx(0.4368)


def test_sproc_packages_match_the_declared_dependencies() -> None:
    """warehouse の依存を pyproject と同じ minor に固定する。

    無指定だと Anaconda channel の最新が入り、**同一SHAでも RMSE がずれる**
    （原因が「Snowflake だけ数値が違う」形で出ると調査が長引く）。
    正本は pyproject.toml。ここはその参照側の pin。
    """
    import tomllib

    from platforms.snowflake.adapter import SPROC_PACKAGES

    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = {spec.split(">=")[0]: spec for spec in pyproject["project"]["dependencies"]}

    for package in ("lightgbm", "scikit-learn", "pandas"):
        assert declared[package] in SPROC_PACKAGES, f"{package} の版指定が pyproject とずれている"


def test_register_model_logs_a_restored_model(sink: JsonlRunSink) -> None:
    """artifact URI ではなく「復元したモデル」を登録する（Tier A との差）。"""
    adapter, session, registry = fake.build(sink)

    run = adapter.register_model(ARTIFACT_URI)

    assert session.file.gets == [f"{ARTIFACT_URI}/model.txt"]
    logged = registry.logged[0]
    assert logged["model"] == "booster:model.txt"
    assert logged["model_name"] == CONFIG.model_name
    assert run.params["model_version"] == MODEL_VERSION


def test_register_model_without_artifact_fails(sink: JsonlRunSink) -> None:
    adapter, _, _ = fake.build(sink, FakeSession(model_written=False))

    run = adapter.register_model(ARTIFACT_URI)

    assert run.status is Status.FAILURE


def test_deploy_creates_no_infrastructure(sink: JsonlRunSink) -> None:
    """Snowflake には「エンドポイントを立てる」に相当する操作が無い。"""
    adapter, session, registry = fake.build(sink)

    run = adapter.deploy(MODEL_VERSION)

    assert run.status is Status.SUCCESS
    assert run.params["no_endpoint_resource"] is True
    assert registry.model.default_assignments == [MODEL_VERSION]
    # DDL も CALL も発行しない = 作られるインフラが無い
    assert session.statements == []


def test_predict_one_uses_warehouse_inference(sink: JsonlRunSink) -> None:
    adapter, _, registry = fake.build(sink)
    adapter.model_version = MODEL_VERSION

    run = adapter.predict_one({"MedInc": 8.3})

    assert run.metrics["prediction"] == pytest.approx(4.2)
    assert registry.version.runs[0]["function_name"] == CONFIG.predict_function


def test_predict_one_with_empty_predictions_fails(sink: JsonlRunSink) -> None:
    registry = FakeRegistry()
    registry.version.predictions = []
    adapter, _, _ = fake.build(sink, registry=registry)
    adapter.model_version = MODEL_VERSION

    run = adapter.predict_one({"MedInc": 8.3})

    assert run.status is Status.FAILURE


def test_teardown_records_fail_safe_as_unavoidable_residual(sink: JsonlRunSink) -> None:
    adapter, session, _ = fake.build(sink)
    adapter.model_version = MODEL_VERSION

    run = adapter.teardown()

    assert session.statements[0].startswith(
        f"DROP PROCEDURE IF EXISTS {CONFIG.qualified_procedure}"
    )
    assert run.params["residual_model_version"] == f"{CONFIG.qualified_model}:{MODEL_VERSION}"
    assert run.params["residual_stage_path"] == CONFIG.package_stage_path
    # 設定で消せない残留（Tier A と同じ土俵で数えない）
    assert run.params["residual_fail_safe_days"] == 7


# --- 接続パラメータの組み立て -------------------------------------------
#
# 実クラウド接続の入口。ここが間違っていると Phase 5 は1手目で止まる。
# 注入セッション（session=）のテストだけでは通ってしまう領域なので分けて固定する。

ENV_KEYPAIR = {
    "SNOWFLAKE_ACCOUNT": "abcdefg-hi12345",
    "SNOWFLAKE_USER": "MCML_LAB_USER",
    "SNOWFLAKE_PRIVATE_KEY_PATH": "/keys/sf.p8",
}


def test_connection_parameters_come_from_env_not_from_connections_toml() -> None:
    """connector に account / user の env フォールバックは無い（実測）。

    `configs()` に値を渡すと connections.toml も読まれなくなるため、
    ここで組まないと `251005: User is empty` で接続できない。
    """
    parameters = connection_parameters(fake.config(role="MCML_DEV_ROLE"), ENV_KEYPAIR)

    assert parameters["account"] == "abcdefg-hi12345"
    assert parameters["user"] == "MCML_LAB_USER"
    assert parameters["database"] == "MCML_DEV"
    assert parameters["schema"] == "ML"
    assert parameters["warehouse"] == "MCML_DEV_WH"


def test_role_comes_from_config_not_from_snowflake_role_env() -> None:
    """**この表明がラボの計測を守っている。**

    `SNOWFLAKE_ROLE` は Terraform provider 用（ACCOUNTADMIN）。adapter まで
    同じ権限で動かすと権限エラーが一度も起きず、本命の permission friction が
    Snowflake だけ常にゼロになる。
    """
    environ = {**ENV_KEYPAIR, "SNOWFLAKE_ROLE": "ACCOUNTADMIN"}

    parameters = connection_parameters(fake.config(role="MCML_DEV_ROLE"), environ)

    assert parameters["role"] == "MCML_DEV_ROLE"
    assert "ACCOUNTADMIN" not in parameters.values()


def test_role_is_omitted_when_outputs_have_none() -> None:
    """apply 前は role_name が無い。既定ロールに委ねる（勝手に埋めない）。"""
    parameters = connection_parameters(fake.config(), ENV_KEYPAIR)

    assert "role" not in parameters


def test_key_pair_sets_the_authenticator_explicitly() -> None:
    """private_key を渡しただけでは AuthByKeyPair が選ばれない（connector 4.7）。"""
    parameters = connection_parameters(fake.config(), ENV_KEYPAIR)

    assert parameters["authenticator"] == "SNOWFLAKE_JWT"
    assert parameters["private_key_file"] == "/keys/sf.p8"


def test_pem_is_converted_to_der_because_connector_base64_decodes_str() -> None:
    """PEM 文字列をそのまま private_key に渡すと base64 DER として復号されて壊れる。"""
    serialization = pytest.importorskip(
        "cryptography.hazmat.primitives.serialization",
        reason="cryptography は snowflake extra 経由でのみ入る",
    )
    rsa = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.rsa")

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    parameters = connection_parameters(
        fake.config(),
        {
            "SNOWFLAKE_ACCOUNT": "abcdefg-hi12345",
            "SNOWFLAKE_USER": "MCML_LAB_USER",
            "SNOWFLAKE_PRIVATE_KEY": pem,
        },
    )

    assert isinstance(parameters["private_key"], bytes)
    # DER として読み戻せる = connector が受ける形
    serialization.load_der_private_key(parameters["private_key"], password=None)


def test_password_is_the_last_resort() -> None:
    """単要素パスワードは塞がれうる。使えてしまう間は落とさず記録に回す。"""
    parameters = connection_parameters(
        fake.config(),
        {
            "SNOWFLAKE_ACCOUNT": "abcdefg-hi12345",
            "SNOWFLAKE_USER": "MCML_LAB_USER",
            "SNOWFLAKE_PASSWORD": "secret",
        },
    )

    assert parameters["password"] == "secret"
    assert "authenticator" not in parameters


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({}, "SNOWFLAKE_ACCOUNT"),
        ({"SNOWFLAKE_ACCOUNT": "abcdefg-hi12345"}, "SNOWFLAKE_USER"),
        (
            {"SNOWFLAKE_ACCOUNT": "abcdefg-hi12345", "SNOWFLAKE_USER": "MCML_LAB_USER"},
            "SNOWFLAKE_PRIVATE_KEY",
        ),
    ],
)
def test_missing_credentials_name_the_variable_to_fill(
    environ: dict[str, str], expected: str
) -> None:
    """「接続できない」ではなく「どの環境変数が無いか」で落とす。"""
    with pytest.raises(SnowflakeConnectionError, match=expected):
        connection_parameters(fake.config(), environ)


def test_register_passes_conda_dependencies(sink: JsonlRunSink) -> None:
    """**conda を明示しないと外部アクセスが要る pip 経路になる。**

    既定（capability `ENABLE_PIP_ONLY_PACKAGING=true`）では manifest が
    `SNOWFLAKE.SNOWPARK.PYPI_SHARED_REPOSITORY` を指し、`CREATE MODEL` 時に
    サーバーが PyPI を取りに行く。トライアルは external access 不可なので
    `603 internal error` になり、**クライアント側は全て成功しているため
    原因が一切出ない**（2026-08-01 に 7 仮説を潰してようやく特定）。
    """
    adapter, _, registry = fake.build(sink)

    adapter.register_model(ARTIFACT_URI)

    logged = registry.logged[0]
    assert logged["conda_dependencies"], "conda_dependencies が空だと pip 経路になる"
    assert any("scikit-learn" in d for d in logged["conda_dependencies"])
    # signature 用の入力サンプルも必須（無いと ValueError 2110）
    assert logged["sample_input_data"] is not None


def test_model_conda_dependencies_are_pinned_exactly() -> None:
    """conda 経路の版は **Anaconda channel の在庫に一致する完全一致 pin** にする。

    範囲指定だと channel に無い版へ解決されうる。pyproject の minor 範囲内に
    収まっていることも併せて確認する。
    """
    from platforms.snowflake.adapter import MODEL_CONDA_DEPENDENCIES

    assert all("==" in d for d in MODEL_CONDA_DEPENDENCIES)
    assert any(d.startswith("scikit-learn==1.8.") for d in MODEL_CONDA_DEPENDENCIES)
    assert any(d.startswith("lightgbm==4.6.") for d in MODEL_CONDA_DEPENDENCIES)
