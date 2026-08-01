"""Azure ML adapter の**固有**検証。共通契約は test_adapter_contract.py。

Azure ML でしか成り立たない形:

  - 入出力が `${{inputs.x}}` / `${{outputs.y}}` のマウント宣言で解決される
  - 登録に承認もエイリアスも無い（名前 + 自動採番）
  - 推論は Endpoint + Deployment の2階層、**トラフィック配分が別操作**
  - 1件推論が request_file（ファイル渡し）
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.fakes import azureml as fake
from tests.fakes.azureml import ARTIFACT_URI, MODEL_REF, FakeMlClient

from core.telemetry.schemas import Status
from core.telemetry.sinks import JsonlRunSink
from platforms.azureml.adapter import ENTRYPOINT_PATH, SCORING_ROUTE

CONFIG = fake.config()


def test_command_job_uses_mount_placeholders(sink: JsonlRunSink) -> None:
    adapter, client, entities = fake.build(sink)

    run = adapter.submit_training({"num_leaves": 31})

    assert run.status is Status.SUCCESS
    command = client.jobs.submitted[0].kwargs["command"]
    assert command.startswith(f"bash {ENTRYPOINT_PATH}")
    # Vertex=環境変数 / SageMaker=固定パス との差
    assert "--input ${{inputs.data}}" in command
    assert "--output ${{outputs.model}}" in command
    assert json.loads(command.split("--params '")[1].split("'")[0]) == {"num_leaves": 31}
    # ジョブ内から ml_runs を書くための識別子（job_record.py の所有者規約）
    assert f"--run-id {run.run_id}" in command
    assert "--attempt 1" in command
    assert client.jobs.submitted[0].kwargs["compute"] == CONFIG.compute_cluster
    assert entities.of_kind("Environment")[0].kwargs["image"] == CONFIG.training_image_uri
    assert client.jobs.streamed == ["mcml-job-1"]
    assert run.params["model_artifact_uri"] == ARTIFACT_URI


def test_failed_job_status_is_recorded(sink: JsonlRunSink) -> None:
    adapter, _, _ = fake.build(sink, FakeMlClient(job_status="Failed"))

    run = adapter.submit_training({})

    assert run.status is Status.FAILURE
    assert run.failure_class is not None


def test_register_uses_name_and_autoversion(sink: JsonlRunSink) -> None:
    """承認もエイリアスも無い = 5基盤で最も軽い登録。"""
    adapter, client, entities = fake.build(sink)

    run = adapter.register_model(ARTIFACT_URI)

    model = entities.of_kind("Model")[0]
    assert model.kwargs["path"] == ARTIFACT_URI
    assert model.kwargs["name"] == CONFIG.model_name
    assert model.kwargs["type"] == "custom_model"
    assert run.params["model_resource_name"] == f"{CONFIG.model_name}:3"
    assert len(client.models.created) == 1


def test_deploy_declares_scoring_route_and_sets_traffic(sink: JsonlRunSink) -> None:
    """トラフィック配分を忘れると 0% のまま呼べない（他2基盤に無い手数）。"""
    adapter, client, entities = fake.build(sink)

    run = adapter.deploy(MODEL_REF)

    serving_env = next(e for e in entities.of_kind("Environment") if "inference_config" in e.kwargs)
    assert serving_env.kwargs["image"] == CONFIG.serving_image_uri
    assert serving_env.kwargs["inference_config"]["scoring_route"]["path"] == SCORING_ROUTE
    assert len(client.online_deployments.upserts) == 1
    assert len(client.online_endpoints.upserts) == 2  # 作成 + traffic 更新
    assert client.online_endpoints.upserts[-1].traffic == {CONFIG.deployment_name: 100}
    assert run.params["traffic"] == {CONFIG.deployment_name: 100}


def test_predict_one_sends_request_via_temp_file(sink: JsonlRunSink) -> None:
    """ファイル渡しは Azure だけ。呼び出し後に消えることまで含めて契約。"""
    adapter, client, _ = fake.build(sink, FakeMlClient(endpoint_exists=True))

    run = adapter.predict_one({"MedInc": 8.3})

    assert run.metrics["prediction"] == 4.2
    invocation = client.online_endpoints.invocations[0]
    assert invocation["endpoint_name"] == CONFIG.endpoint_name
    assert invocation["request_file"].endswith("request.json")
    assert not Path(invocation["request_file"]).exists()


def test_predict_one_with_empty_predictions_fails(sink: JsonlRunSink) -> None:
    client = FakeMlClient(endpoint_exists=True)
    client.online_endpoints.response = json.dumps({"predictions": []})
    adapter, _, _ = fake.build(sink, client)

    run = adapter.predict_one({"MedInc": 8.3})

    assert run.status is Status.FAILURE


def test_teardown_deletes_endpoint_and_keeps_model(sink: JsonlRunSink) -> None:
    adapter, client, _ = fake.build(sink)
    adapter.register_model(ARTIFACT_URI)
    adapter.deploy(MODEL_REF)

    run = adapter.teardown()

    assert client.online_endpoints.deleted == [CONFIG.endpoint_name]
    assert run.params["removed"] == [f"endpoint:{CONFIG.endpoint_name}"]
    # Model は残る = Azure 側の残留
    assert run.params["residual_model"] == f"{CONFIG.model_name}:3"
