"""Vertex AI adapter。

Terraform でカバー: IAM / GCS / Artifact Registry / Endpoint（器）
SDK に残る      : Custom Job / Experiment / Model Upload / Deploy

実行契約: AIP_MODEL_DIR に成果物を書く / 引数は自由。
entrypoint シムは docker/training/entrypoint_vertex.sh。

移植元: ML/kaggle-bronze-gcp/src/runner/{experiment/vertex_run.py, model/register.py,
model/deploy.py}（流用元: kaggle-bronze-gcp）

移植時に変えた点:
  - YAML 設定の読み込みを持たない。設定は VertexConfig（呼び出し側が組む）。
    流用元は CLI ツールなので config ファイル解決を内蔵していたが、
    ここは adapter であり、5基盤で同じ形（PlatformAdapter）に揃える方が優先。
  - 各操作を track() で包み **必ず ml_runs 1行**にする。失敗しても記録してから
    MlRun を返す（例外を投げっぱなしにすると permission friction が測れない
    = ports.PlatformAdapter の契約）。
  - 出力先は `--output-uri` ではなく **AIP_MODEL_DIR**（base_output_dir 経由）。
    Tier A 3基盤のシムを同じ形に揃えるため。
  - guarded sync（ログ無音検知 cancel）は移植しない。流用元は gcloud/logging を
    subprocess で叩いており、docker/training の「外部 CLI を呼ばない」方針と衝突する。
    タイムアウトは CustomJob の timeout に委ねる。

SDK は遅延 import する。core は import しない（tests/test_core_boundaries.py）が、
platforms 側でも import 時点で google-cloud-aiplatform を要求すると、
SDK 未インストール環境（他基盤のコンテナ・CI）で adapter を読めなくなる。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from core.telemetry.schemas import MlRun, Platform, Stage, Tier, UnificationUnit
from core.telemetry.tracking import RunContext, RunSink
from platforms.shared.contracts.tracking import TrackedOperations, artifact_uri_from, telemetry_env

_logger = logging.getLogger("platforms.vertex")

# 学習イメージ内でのシムの配置。docker/training/Dockerfile の COPY 先と一致させる。
ENTRYPOINT_PATH = "/app/entrypoint_vertex.sh"

# Vertex 学習コンテナは gs://<bucket> を /gcs/<bucket> に FUSE マウントする。
GCS_FUSE_ROOT = "/gcs"


@dataclass(frozen=True)
class VertexConfig:
    """Terraform の outputs（infra/environments/gcp-dev）から埋める。"""

    project: str
    region: str
    bucket: str
    training_image_uri: str
    serving_image_uri: str | None = None
    service_account: str | None = None
    data_prefix: str = "data/california_housing"
    machine_type: str = "n1-standard-4"
    use_spot: bool = True
    timeout_hours: float = 2.0
    model_display_name: str = "mcml-california-housing"
    endpoint_display_name: str = "mcml-dev-endpoint"
    endpoint_machine_type: str = "n1-standard-2"
    # Vertex AI Experiments への複写先。**None なら複写しない**（既定 OFF）。
    # env で有効化するなら MCML_VERTEX_EXPERIMENT（config.py の解決規約そのまま）。
    # Vertex 固有の関心なので、共通層ではなくこの config が持つ（ports.py の
    # 「5基盤ぶんの実装が並ぶものだけ port にする」に従う）。
    experiment: str | None = None
    # パイプラインのステップに使う器（docker/orchestrator/Dockerfile）。
    # 未指定なら training と同じレジストリの orchestrator を使う
    # （platforms/vertex/pipeline.py の orchestrator_image()）。
    orchestrator_image_uri: str | None = None
    # 推論契約（src/core/app/api/routes.py の Vertex 用ルート）
    predict_route: str = "/predict"
    health_route: str = "/health"
    serving_port: int = 8080
    labels: dict[str, str] = field(default_factory=lambda: {"app": "multicloud-ml-platform-lab"})


class VertexAdapter(TrackedOperations):
    """Golden Path ステップ2〜3 の Vertex 実装。

    使い方:

        adapter = VertexAdapter(config, sink=sink)
        train = adapter.submit_training({"num_leaves": 31})
        register = adapter.register_model(adapter.model_artifact_uri(train))
        adapter.deploy(adapter.model_resource_name)
        adapter.predict_one({...})
        adapter.teardown()   # 常時課金なのでフェーズ末に必ず
    """

    platform = Platform.VERTEX
    tier = Tier.A
    unification_unit = UnificationUnit.CONTAINER

    def __init__(
        self,
        config: VertexConfig,
        *,
        sink: RunSink,
        aiplatform: Any | None = None,
    ) -> None:
        self._config = config
        self._sink = sink
        self._aiplatform = aiplatform
        self._experiments: Any | None = None  # 遅延生成（config.experiment が無ければ使わない）
        self.job_resource_name: str | None = None
        self.model_resource_name: str | None = None
        self.endpoint_resource_name: str | None = None

    # --- SDK 解決 ---------------------------------------------------------

    @property
    def sdk(self) -> Any:
        """google.cloud.aiplatform を遅延 import して init 済みで返す。"""
        if self._aiplatform is None:
            from google.cloud import aiplatform  # noqa: PLC0415 - 遅延 import は意図的

            aiplatform.init(
                project=self._config.project,
                location=self._config.region,
                staging_bucket=f"gs://{self._config.bucket}",
            )
            self._aiplatform = aiplatform
        return self._aiplatform

    # --- Vertex 固有の観測（Experiments 複写） ---------------------------

    def _tracked(self, *args: Any, **kwargs: Any) -> MlRun:
        """共通の記録（super）を済ませた後、**Vertex 固有の観測**を足す。

        ここが adapter 内 override であることに意味がある。Experiments は Vertex の
        サービスで、5基盤に並ぶ実装が無い。共通層（TrackedOperations / factory）へ
        フックを置くと「5基盤に共通する形だけを共通層に置く」原則（ports.py）が崩れる
        —— 2026-08-02 に一度そうして作り直した。

        `job_owns_success` で Neon への成功行が抑制されるケース（学習）でも、
        super() が返す run はここに届くので**学習成功行も観測される**。
        観測は非致命（複写に失敗しても計測結果と戻り値は変えない）。
        """
        run = super()._tracked(*args, **kwargs)
        if self._config.experiment:
            try:
                if self._experiments is None:
                    from platforms.vertex.experiment_observer import (  # noqa: PLC0415
                        VertexExperimentObserver,
                    )

                    # self.sdk を渡す = aiplatform.init(project/region) 済みの SDK を共有する。
                    # observer が独自に import すると init 前の SDK で create が落ちる
                    self._experiments = VertexExperimentObserver(
                        experiment=self._config.experiment, aiplatform=self.sdk
                    )
                self._experiments.observe(run)
            except Exception:  # noqa: BLE001 - 観測の失敗で計測結果を変えない
                logging.getLogger("platforms.vertex").warning(
                    "Experiments への複写に失敗（記録は済んでいる）", exc_info=True
                )
        return run

    # --- PlatformAdapter -------------------------------------------------

    def submit_training(self, params: dict[str, Any]) -> MlRun:
        """CustomJob を組み立てて投入し、完了まで待つ。

        Spot（scheduling_strategy=SPOT）が既定。中断されたら失敗 run として
        記録されるので、コスト優先の既定にしても計測は壊れない。

        run_id / attempt をジョブへ渡し、**成功時の ml_runs 行はジョブ側が書く**
        （platforms/neon/job_record.py の「run 行の所有者」規約）。
        Neon 接続情報も container_spec.env で渡し、ジョブ内から直接 INSERT させる。
        """
        cfg = self._config
        input_dir = f"{GCS_FUSE_ROOT}/{cfg.bucket}/{cfg.data_prefix}"
        attempt = self._next_attempt(Stage.TRAIN)
        job_env = [{"name": k, "value": v} for k, v in telemetry_env().items()]

        def call(ctx: RunContext) -> None:
            container_spec: dict[str, Any] = {
                "image_uri": cfg.training_image_uri,
                "command": ["bash", ENTRYPOINT_PATH],
                "args": [
                    "--input",
                    input_dir,
                    "--params",
                    json.dumps(params),
                    "--run-id",
                    ctx.run_id,
                    "--attempt",
                    str(attempt),
                ],
            }
            if job_env:
                container_spec["env"] = job_env
            job = self.sdk.CustomJob(
                display_name=f"{cfg.model_display_name}-train",
                worker_pool_specs=[
                    {
                        "machine_spec": {"machine_type": cfg.machine_type},
                        "replica_count": 1,
                        "container_spec": container_spec,
                    }
                ],
                base_output_dir=self.base_output_dir(ctx.run_id),
                labels=cfg.labels,
            )
            kwargs: dict[str, Any] = {"timeout": int(cfg.timeout_hours * 3600), "sync": True}
            if cfg.service_account:
                kwargs["service_account"] = cfg.service_account
            if cfg.use_spot:
                kwargs["scheduling_strategy"] = "SPOT"
            job.run(**kwargs)
            self.job_resource_name = job.resource_name
            ctx.params["job_resource_name"] = job.resource_name
            ctx.params["model_artifact_uri"] = self.model_artifact_uri_for(ctx.run_id)

        return self._tracked(
            Stage.TRAIN, dict(params), call, attempt=attempt, job_owns_success=True
        )

    def register_model(self, artifact_uri: str) -> MlRun:
        """Model.upload。既存の同名モデルがあれば parent にして新バージョンにする。

        serving_image_uri を渡している場合だけ serving 契約（route / port）を付ける。
        付けないと Endpoint へ deploy できない。
        """
        cfg = self._config

        def call(ctx: RunContext) -> None:
            parent = self._find_parent_model()
            upload_kwargs: dict[str, Any] = {
                "display_name": cfg.model_display_name,
                "artifact_uri": artifact_uri,
                "version_aliases": ["latest"],
                "version_description": f"run_id={ctx.run_id}",
                "labels": cfg.labels,
                "parent_model": parent,
                "is_default_version": True,
                "serving_container_image_uri": cfg.serving_image_uri or cfg.training_image_uri,
            }
            if cfg.serving_image_uri:
                upload_kwargs.update(
                    serving_container_predict_route=cfg.predict_route,
                    serving_container_health_route=cfg.health_route,
                    serving_container_ports=[cfg.serving_port],
                )
            model = self.sdk.Model.upload(**upload_kwargs)
            self.model_resource_name = model.resource_name
            ctx.params["model_resource_name"] = model.resource_name
            ctx.params["is_new_version"] = parent is not None

        return self._tracked(Stage.REGISTER, {"artifact_uri": artifact_uri}, call)

    def deploy(self, model_ref: str) -> MlRun:
        """Endpoint を探して無ければ作り、モデルをデプロイする。

        ⚠️ デプロイ中は常時課金。フェーズ末に必ず teardown() を呼ぶ
        （残ると terraform destroy が HTTP 400 で落ちる）。
        """
        cfg = self._config

        def call(ctx: RunContext) -> None:
            model = self.sdk.Model(model_ref)
            endpoints = self.sdk.Endpoint.list(filter=f'display_name="{cfg.endpoint_display_name}"')
            # create() の前に確定させる（後で評価すると、作成結果が混ざった一覧を
            # 見てしまい「再利用した」と誤記録しうる）
            reused = bool(endpoints)
            endpoint = (
                endpoints[0]
                if reused
                else self.sdk.Endpoint.create(display_name=cfg.endpoint_display_name)
            )
            model.deploy(
                endpoint=endpoint,
                deployed_model_display_name=f"{cfg.model_display_name}-deployed",
                machine_type=cfg.endpoint_machine_type,
                min_replica_count=1,
                max_replica_count=1,
                traffic_percentage=100,
                sync=True,
            )
            self.endpoint_resource_name = endpoint.resource_name
            ctx.params["endpoint_resource_name"] = endpoint.resource_name
            ctx.params["reused_endpoint"] = reused

        return self._tracked(Stage.DEPLOY, {"model_ref": model_ref}, call)

    def predict_one(self, instance: dict[str, Any]) -> MlRun:
        """1件だけオンライン推論する（フェーズ完了条件⑤）。"""
        cfg = self._config

        def call(ctx: RunContext) -> None:
            endpoints = self.sdk.Endpoint.list(filter=f'display_name="{cfg.endpoint_display_name}"')
            if not endpoints:
                raise RuntimeError(
                    f"Endpoint が無い（display_name={cfg.endpoint_display_name}）。"
                    "deploy 済みか確認"
                )
            response = endpoints[0].predict(instances=[instance])
            predictions = list(getattr(response, "predictions", response))
            if not predictions:
                raise RuntimeError("predictions が空。serving コンテナの応答形式を確認")
            ctx.metrics["prediction"] = predictions[0]

        return self._tracked(Stage.PREDICT, {"instance": instance}, call)

    def teardown(self) -> MlRun:
        """Endpoint を undeploy_all してから delete(force)。

        Endpoint が残ると terraform destroy が HTTP 400 で落ちる
        （ML/gcp-search-mlops-gke/scripts/domain/gcp/vertex_cleanup.py の教訓）。
        対象が無い場合も成功として記録する（撤退の冪等性）。
        """
        cfg = self._config

        def call(ctx: RunContext) -> None:
            endpoints = self.sdk.Endpoint.list(filter=f'display_name="{cfg.endpoint_display_name}"')
            removed = []
            for endpoint in endpoints:
                endpoint.undeploy_all()
                endpoint.delete(force=True)
                removed.append(endpoint.resource_name)
            self.endpoint_resource_name = None
            ctx.params["removed_endpoints"] = removed

        # Stage に teardown は無い（DDL 正本 sql/schema.sql に合わせている）。
        # デプロイのライフサイクル操作なので deploy 側に寄せ、params で action を区別する。
        return self._tracked(Stage.TEARDOWN, {"action": "teardown"}, call)

    # --- 補助 -------------------------------------------------------------

    def base_output_dir(self, run_id: str) -> str:
        """CustomJob の base_output_dir。Vertex はこの下に AIP_MODEL_DIR を作る。"""
        return f"gs://{self._config.bucket}/runs/{run_id}"

    def model_artifact_uri_for(self, run_id: str) -> str:
        """register_model へ渡す成果物 URI（AIP_MODEL_DIR と同じ場所）。"""
        return f"{self.base_output_dir(run_id)}/model"

    def model_artifact_uri(self, train_run: MlRun) -> str:
        """train の MlRun から成果物 URI を取り出す（5基盤共通）。"""
        return artifact_uri_from(train_run)

    def _find_parent_model(self) -> str | None:
        models = self.sdk.Model.list(filter=f'display_name="{self._config.model_display_name}"')
        return models[0].resource_name if models else None
