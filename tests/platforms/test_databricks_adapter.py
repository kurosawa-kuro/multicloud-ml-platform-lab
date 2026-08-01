"""Databricks adapter の**固有**検証。共通契約は test_adapter_contract.py。

Tier B（Databricks）でしか成り立たない形:

  - ジョブ定義は Terraform 側。adapter は **名前から ID を引いて起動するだけ**
  - モデルは UC の3階層名前空間（catalog.schema.model）
  - serving は **scale_to_zero_enabled 必須**（アイドル課金比較の核心）
  - 1件推論が dataframe_records（HTTP 3契約を通らない）
  - teardown 後も UC のモデル版と Volume 上の wheel が残る
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.fakes import databricks as fake
from tests.fakes.databricks import MODEL_ARTIFACT, MODEL_VERSION, FakeWorkspaceClient

from core.telemetry.schemas import Status
from core.telemetry.sinks import JsonlRunSink

CONFIG = fake.config()


def test_uc_three_level_namespace() -> None:
    """モデルがカタログの中に居ることが名前にそのまま出る（Tier A との構造差）。"""
    assert CONFIG.model_full_name == "mcml_dev.ml.california_housing"
    assert CONFIG.volume_root == "/Volumes/mcml_dev/ml/artifacts"


def test_submit_training_triggers_the_terraform_defined_job(sink: JsonlRunSink) -> None:
    adapter, client, _ = fake.build(sink)

    run = adapter.submit_training({"num_leaves": 31})

    assert run.status is Status.SUCCESS
    # ID の手書きは apply のたびに腐るので名前から引く
    assert client.jobs.list_calls == [CONFIG.job_name]
    call = client.jobs.run_now_calls[0]
    assert call["job_id"] == 101

    # wheel の entry point（platforms.databricks.job_main）へそのまま渡る引数列。
    # `--input` / `--output` が無いと CLI が exit 2 で即死する（Tier A のシムと同じ契約）。
    args = dict(zip(call["python_params"][::2], call["python_params"][1::2], strict=True))
    assert args["--input"] == CONFIG.data_path
    assert args["--output"] == CONFIG.output_path(run.run_id)
    assert args["--run-id"] == run.run_id
    assert args["--attempt"] == "1"  # ジョブ側が ml_runs を書くのに要る（数え直せない）
    assert json.loads(args["--params"]) == {"num_leaves": 31}
    # 成果物パスは **ml_runs.run_id 基準**（Databricks 側 run_id は完了後にしか分からない）
    assert run.params["model_artifact_uri"] == CONFIG.output_path(run.run_id)


def test_missing_job_is_recorded_as_failure(sink: JsonlRunSink) -> None:
    adapter, _, _ = fake.build(sink, FakeWorkspaceClient(jobs=[]))

    run = adapter.submit_training({})

    assert run.status is Status.FAILURE


def test_failed_run_state_is_recorded(sink: JsonlRunSink) -> None:
    adapter, _, _ = fake.build(sink, FakeWorkspaceClient(result_state="FAILED"))

    run = adapter.submit_training({})

    assert run.status is Status.FAILURE
    assert run.failure_class is not None


def test_register_model_runs_the_job_because_the_sdk_cannot_create_versions(
    sink: JsonlRunSink,
) -> None:
    """UC の版は MLflow クライアント経由でしか作れない（SDK に create が無い）。

    そのため④は「API 1本」ではなく **Terraform 定義のジョブを `--stage register`
    で起こす**。この構造差（起動オーバーヘッドを伴う登録）が比較表の材料になる。
    """
    adapter, client, _ = fake.build(sink)

    run = adapter.register_model(MODEL_ARTIFACT)

    args = dict(
        zip(
            client.jobs.run_now_calls[0]["python_params"][::2],
            client.jobs.run_now_calls[0]["python_params"][1::2],
            strict=True,
        )
    )
    assert args["--stage"] == "register"
    assert args["--output"] == MODEL_ARTIFACT
    assert args["--model-name"] == "mcml_dev.ml.california_housing"
    # 版番号はジョブの stdout ではなく UC を引き直して確定させる
    assert client.model_versions.list_calls == ["mcml_dev.ml.california_housing"]
    assert run.params["model_version"] == MODEL_VERSION
    assert adapter.model_version == MODEL_VERSION


def test_register_model_fails_when_no_version_appears(sink: JsonlRunSink) -> None:
    """ジョブが成功しても UC に版が無ければ失敗として記録する（緑の嘘を作らない）。"""
    adapter, _, _ = fake.build(sink, FakeWorkspaceClient(registers_version=False))

    run = adapter.register_model(MODEL_ARTIFACT)

    assert run.status is Status.FAILURE


def test_deploy_always_enables_scale_to_zero(sink: JsonlRunSink) -> None:
    """落とすと Tier A との比較の前提が変わる。"""
    adapter, client, entities = fake.build(sink)

    run = adapter.deploy(MODEL_VERSION)

    served = entities.of_kind("ServedEntityInput")[0].kwargs
    assert served["entity_name"] == "mcml_dev.ml.california_housing"
    assert served["entity_version"] == MODEL_VERSION
    assert served["scale_to_zero_enabled"] is True
    assert served["workload_size"] == CONFIG.serving_workload_size
    assert len(client.serving_endpoints.created) == 1
    assert run.params["reused_endpoint"] is False


def test_deploy_updates_existing_endpoint(sink: JsonlRunSink) -> None:
    adapter, client, _ = fake.build(sink, FakeWorkspaceClient(endpoint_exists=True))

    run = adapter.deploy(MODEL_VERSION)

    assert len(client.serving_endpoints.updated) == 1
    assert client.serving_endpoints.created == []
    assert run.params["reused_endpoint"] is True


def test_predict_one_uses_dataframe_records(sink: JsonlRunSink) -> None:
    adapter, client, _ = fake.build(sink, FakeWorkspaceClient(endpoint_exists=True))

    run = adapter.predict_one({"MedInc": 8.3})

    assert run.metrics["prediction"] == 4.2
    # 基盤が直接モデルを配信するので自前の推論コンテナが登場しない
    assert client.serving_endpoints.queries[0]["dataframe_records"] == [{"MedInc": 8.3}]


def test_predict_one_with_empty_predictions_fails(sink: JsonlRunSink) -> None:
    client = FakeWorkspaceClient(endpoint_exists=True)
    client.serving_endpoints.predictions = []
    adapter, _, _ = fake.build(sink, client)

    run = adapter.predict_one({"MedInc": 8.3})

    assert run.status is Status.FAILURE


def test_teardown_records_tier_b_residuals(sink: JsonlRunSink) -> None:
    """teardown 後に残るのは **Volume 上の wheel だけ**。

    モデル版は destroy を止めるので teardown で消す（下の専用テスト参照）。
    """
    adapter, client, _ = fake.build(sink, FakeWorkspaceClient(endpoint_exists=True))
    adapter.register_model(MODEL_ARTIFACT)

    run = adapter.teardown()

    assert client.serving_endpoints.deleted == [CONFIG.serving_endpoint_name]
    assert run.params["residual_model_version"] is None
    assert run.params["residual_wheel"].endswith(CONFIG.wheel_filename)


def test_upload_wheel_targets_the_volume_path(sink: JsonlRunSink, tmp_path: Path) -> None:
    adapter, client, _ = fake.build(sink)
    wheel = tmp_path / CONFIG.wheel_filename
    wheel.write_bytes(b"fake wheel")

    remote = adapter.upload_wheel(wheel)

    assert remote == f"{CONFIG.volume_root}/dist/{CONFIG.wheel_filename}"
    assert client.files.uploads == [remote]


def test_register_passes_an_experiment_path(sink: JsonlRunSink) -> None:
    """wheel task には既定の MLflow 実験が無い。

    渡さないと登録が `RESOURCE_DOES_NOT_EXIST: No experiment was found` で落ちる
    （2026-08-01 実測）。Free Edition は `/Shared` が無いことがあるのでユーザーのホーム。
    """
    adapter, client, _ = fake.build(sink)

    adapter.register_model(MODEL_ARTIFACT)

    params = client.jobs.run_now_calls[0]["python_params"]
    args = dict(zip(params[::2], params[1::2], strict=True))
    assert args["--experiment"] == "/Users/owner@example.com/mcml-lab"


def test_deploy_names_the_endpoint_config(sink: JsonlRunSink) -> None:
    """`EndpointCoreConfigInput` は name 必須（SDK 0.123 実測）。省くと1つも作れない。"""
    adapter, _, entities = fake.build(sink)

    adapter.deploy(MODEL_VERSION)

    config_kwargs = entities.of_kind("EndpointCoreConfigInput")[0].kwargs
    assert config_kwargs["name"] == CONFIG.serving_endpoint_name


def test_teardown_deletes_uc_model_versions(sink: JsonlRunSink) -> None:
    """版を残すと **terraform destroy が止まる**（force_destroy が無い）。

    2026-08-01 実測: `cannot delete registered model: ... has 2 model versions(s)`。
    版を作るのは SDK/MLflow 側なので、作った層が片付ける。
    """
    adapter, client, _ = fake.build(sink, FakeWorkspaceClient(endpoint_exists=True))
    adapter.register_model(MODEL_ARTIFACT)

    run = adapter.teardown()

    assert client.model_versions.deleted == [("mcml_dev.ml.california_housing", int(MODEL_VERSION))]
    assert run.params["residual_model_version"] is None
