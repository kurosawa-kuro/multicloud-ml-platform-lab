"""Vertex AI adapter の**固有**検証。

共通契約（1操作1行 / 失敗を投げない / attempt / teardown 冪等）は
tests/test_adapter_contract.py が5基盤まとめて見る。ここに置くのは
**Vertex でしか成り立たない形**だけ:

  - CustomJob が entrypoint シムの契約（command / args）どおりに組まれる
  - 出力先が base_output_dir（= AIP_MODEL_DIR の親）で解決される
  - Model.upload の版管理（parent_model / version_aliases / serving 契約）
  - Endpoint は**器を先に作れる**（再利用と新規作成の判定）
  - teardown が undeploy_all → delete(force) の順
"""

from __future__ import annotations

import json

from tests.fakes import vertex as fake
from tests.fakes.vertex import FakeEndpoint, FakeModel, FakeSdk

from core.telemetry.schemas import FailureClass, Stage, Status
from core.telemetry.sinks import JsonlRunSink
from platforms.vertex.adapter import ENTRYPOINT_PATH

CONFIG = fake.config()


def test_custom_job_matches_the_entrypoint_contract(sink: JsonlRunSink) -> None:
    adapter, sdk = fake.build(sink)

    run = adapter.submit_training({"num_leaves": 31})

    assert run.status is Status.SUCCESS
    spec = sdk.jobs[0].kwargs["worker_pool_specs"][0]["container_spec"]
    assert spec["image_uri"] == CONFIG.training_image_uri
    assert spec["command"] == ["bash", ENTRYPOINT_PATH]
    # シムは --input と --params だけを受ける（--output は AIP_MODEL_DIR から解決）
    assert spec["args"][0] == "--input"
    assert spec["args"][1] == f"/gcs/{CONFIG.bucket}/{CONFIG.data_prefix}"
    assert spec["args"][2] == "--params"
    assert json.loads(spec["args"][3]) == {"num_leaves": 31}


def test_output_dir_and_spot_are_set_on_the_job(sink: JsonlRunSink) -> None:
    adapter, sdk = fake.build(sink)

    adapter.submit_training({})

    job = sdk.jobs[0]
    assert job.kwargs["base_output_dir"].startswith(f"gs://{CONFIG.bucket}/runs/")
    assert job.run_kwargs is not None
    assert job.run_kwargs["scheduling_strategy"] == "SPOT"
    assert job.run_kwargs["service_account"] == CONFIG.service_account


def test_train_run_exposes_artifact_uri_for_register(sink: JsonlRunSink) -> None:
    adapter, _ = fake.build(sink)

    run = adapter.submit_training({})

    assert adapter.model_artifact_uri(run).endswith("/model")


def test_permission_error_is_classified_as_iam(sink: JsonlRunSink) -> None:
    """failure_class の推定が効くこと（SDK 止まりだと friction が読めない）。"""
    adapter = fake.case().make_failing(sink)

    run = adapter.submit_training({})

    assert run.status is Status.FAILURE
    assert run.failure_class is FailureClass.IAM


def test_register_creates_a_new_version_when_parent_exists(sink: JsonlRunSink) -> None:
    sdk = FakeSdk(existing_models=[FakeModel("projects/p/locations/r/models/parent")])
    adapter, _ = fake.build(sink, sdk)

    run = adapter.register_model("gs://b/runs/x/model")

    uploaded = sdk.uploaded[0]
    assert uploaded["parent_model"] == "projects/p/locations/r/models/parent"
    assert uploaded["version_aliases"] == ["latest"]
    assert uploaded["serving_container_predict_route"] == "/predict"
    assert uploaded["serving_container_health_route"] == "/health"
    assert uploaded["serving_container_ports"] == [8080]
    assert run.params["is_new_version"] is True
    assert run.stage is Stage.REGISTER


def test_register_without_serving_image_skips_routes(sink: JsonlRunSink) -> None:
    """serving イメージが無ければ推論契約を付けない（deploy 不能な版を作らない）。"""
    sdk = FakeSdk()
    adapter, _ = fake.build(sink, sdk, serving_image_uri=None)

    adapter.register_model("gs://b/runs/x/model")

    uploaded = sdk.uploaded[0]
    assert uploaded["serving_container_image_uri"] == CONFIG.training_image_uri
    assert "serving_container_predict_route" not in uploaded


def test_deploy_reuses_the_endpoint_shell(sink: JsonlRunSink) -> None:
    """Vertex だけが「器を先に作れる」。再利用の判定を固定する。"""
    existing = FakeEndpoint()
    adapter, sdk = fake.build(sink, FakeSdk(existing_endpoints=[existing]))

    run = adapter.deploy("projects/p/locations/r/models/1")

    assert run.params["reused_endpoint"] is True
    assert sdk.created_endpoints == []
    assert sdk.model_instance.deploy_kwargs is not None
    assert sdk.model_instance.deploy_kwargs["traffic_percentage"] == 100
    assert adapter.endpoint_resource_name == existing.resource_name


def test_deploy_creates_endpoint_when_missing(sink: JsonlRunSink) -> None:
    adapter, sdk = fake.build(sink)

    run = adapter.deploy("projects/p/locations/r/models/1")

    assert run.params["reused_endpoint"] is False
    assert len(sdk.created_endpoints) == 1


def test_predict_one_records_the_prediction(sink: JsonlRunSink) -> None:
    endpoint = FakeEndpoint()
    adapter, _ = fake.build(sink, FakeSdk(existing_endpoints=[endpoint]))

    run = adapter.predict_one({"MedInc": 8.3})

    assert run.metrics["prediction"] == 4.2
    assert "predict(1)" in endpoint.calls


def test_predict_one_without_endpoint_fails(sink: JsonlRunSink) -> None:
    adapter, _ = fake.build(sink)

    run = adapter.predict_one({"MedInc": 8.3})

    assert run.status is Status.FAILURE


def test_predict_one_with_empty_predictions_fails(sink: JsonlRunSink) -> None:
    endpoint = FakeEndpoint()
    endpoint.predictions = []
    adapter, _ = fake.build(sink, FakeSdk(existing_endpoints=[endpoint]))

    run = adapter.predict_one({"MedInc": 8.3})

    assert run.status is Status.FAILURE


def test_teardown_undeploys_before_delete(sink: JsonlRunSink) -> None:
    """順序が逆だと HTTP 400 で落ちる（gcp-search-mlops-gke の教訓）。"""
    endpoint = FakeEndpoint()
    adapter, _ = fake.build(sink, FakeSdk(existing_endpoints=[endpoint]))

    run = adapter.teardown()

    assert endpoint.calls == ["undeploy_all", "delete(force=True)"]
    assert run.params["removed_endpoints"] == [endpoint.resource_name]
    assert adapter.endpoint_resource_name is None
