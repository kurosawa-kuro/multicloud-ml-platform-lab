"""SageMaker AI adapter。

Terraform でカバー: S3 / ECR / IAM Role / Model Package Group /
                    Model / Endpoint Config / Endpoint（学習後の2段階目）
SDK に残る        : Training Job / Model Registry 登録・承認

実行契約（3基盤で最も厳しい・パス固定）:
    /opt/ml/input/data/<channel>               入力
    /opt/ml/input/config/hyperparameters.json  ハイパーパラメータ
    /opt/ml/model                              モデル出力（S3 へ回収される）
    /opt/ml/output/failure                     失敗理由
推論は port 8080 の /ping + /invocations。
entrypoint シムは docker/training/entrypoint_sagemaker.sh。

移植元: ML/eks-app-mlops-platform-v2/mlops/src/train.py（boto3 の S3 往復パターン）+
TMP/amazon-sagemaker-ml-pipeline-deploy-with-terraform（Training → Model →
EndpointConfig → Endpoint の依存順序）。
docs/tasks/02_backlog/reuse-asset-import-map.md A-2。

**sagemaker Python SDK ではなく boto3 の低レベル API を使う。** 理由は2つ:
  - SDK の Estimator は script mode を前提に多くを隠す。本ラボは BYOC 統一が要件
    （docs/01_requirements.md「SageMaker script mode に逃げない」）で、隠れると比較にならない。
  - Terraform 側（infra/modules/aws）も Model / EndpointConfig / Endpoint を持てる。
    **同じリソースを両経路で作れる**ことが比較材料なので、低レベル API の同じ形に
    落としたほうが読み比べやすい。

Vertex との差（比較レポート行き）:
  - Vertex は Endpoint の器を先に作れるが、SageMaker は Model → EndpointConfig →
    Endpoint と積み上げる必要がある（deploy() が3リソースを作る）。
  - ハイパーパラメータは引数ではなくファイル（hyperparameters.json）で渡り、
    **値は文字列しか入らない**。型を保つため JSON 文字列を1キーに畳み、シムが展開する。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from core.telemetry.schemas import MlRun, Platform, Stage, Tier, UnificationUnit
from core.telemetry.tracking import RunContext, RunSink
from platforms.shared.contracts.tracking import (
    TrackedOperations,
    artifact_uri_from,
    is_missing,
    telemetry_env,
)

_logger = logging.getLogger("platforms.sagemaker")

# 学習イメージ内でのシムの配置。docker/training/Dockerfile の COPY 先と一致させる。
ENTRYPOINT_PATH = "/app/entrypoint_sagemaker.sh"

# 入力チャネル名。コンテナ内では /opt/ml/input/data/<channel> になる。
TRAINING_CHANNEL = "training"

# hyperparameters.json は値が文字列限定。型を保つため JSON をこのキーに畳む。
PARAMS_HYPERPARAMETER_KEY = "params"

# 学習ジョブの中から ml_runs を書くために必要な識別子。
# 引数で渡せない（SageMaker はハイパーパラメータしか渡せない）ので同じ経路に載せる。
RUN_ID_HYPERPARAMETER_KEY = "run_id"
ATTEMPT_HYPERPARAMETER_KEY = "attempt"


@dataclass(frozen=True)
class SageMakerConfig:
    """Terraform の outputs（infra/environments/aws-dev）から埋める。"""

    region: str
    bucket: str
    execution_role_arn: str
    training_image_uri: str
    serving_image_uri: str | None = None
    model_package_group_name: str = "mcml-dev-models"
    data_prefix: str = "data/california_housing"
    output_prefix: str = "runs"
    instance_type: str = "ml.m5.large"
    instance_count: int = 1
    volume_size_gb: int = 30
    max_runtime_seconds: int = 7200
    use_spot: bool = True
    # Spot は中断待ちを含む上限。max_runtime_seconds より短くできない。
    max_wait_seconds: int = 10800
    endpoint_name: str = "mcml-dev-endpoint"
    endpoint_instance_type: str = "ml.t2.medium"
    endpoint_instance_count: int = 1
    job_prefix: str = "mcml-dev"
    tags: dict[str, str] = field(default_factory=lambda: {"Project": "multicloud-ml-platform-lab"})


class SageMakerAdapter(TrackedOperations):
    """Golden Path ステップ2〜3 の SageMaker 実装。

    使い方:

        adapter = SageMakerAdapter(config, sink=sink)
        train = adapter.submit_training({"num_leaves": 31})
        adapter.register_model(adapter.model_artifact_uri(train))
        adapter.deploy(adapter.model_artifact_uri(train))
        adapter.predict_one({...})
        adapter.teardown()   # Endpoint は常時課金
    """

    platform = Platform.SAGEMAKER
    tier = Tier.A
    unification_unit = UnificationUnit.CONTAINER

    def __init__(
        self,
        config: SageMakerConfig,
        *,
        sink: RunSink,
        sagemaker_client: Any | None = None,
        runtime_client: Any | None = None,
    ) -> None:
        self._config = config
        self._sink = sink
        self._sagemaker = sagemaker_client
        self._runtime = runtime_client
        self.training_job_name: str | None = None
        self.model_package_arn: str | None = None
        self.model_name: str | None = None
        self.endpoint_config_name: str | None = None

    # --- SDK 解決（遅延 import）-------------------------------------------

    @property
    def sagemaker(self) -> Any:
        if self._sagemaker is None:
            import boto3  # noqa: PLC0415 - 遅延 import は意図的

            self._sagemaker = boto3.client("sagemaker", region_name=self._config.region)
        return self._sagemaker

    @property
    def runtime(self) -> Any:
        if self._runtime is None:
            import boto3  # noqa: PLC0415

            self._runtime = boto3.client("sagemaker-runtime", region_name=self._config.region)
        return self._runtime

    # --- PlatformAdapter -------------------------------------------------

    def submit_training(self, params: dict[str, Any]) -> MlRun:
        """Training Job を投入し、完了を待って成果物 URI を確定する。

        Managed Spot が既定。中断されたら失敗 run として記録されるので、
        コスト優先の既定でも計測は壊れない。

        run_id / attempt は hyperparameters（文字列限定）に載せてシムへ渡し、
        **成功時の ml_runs 行はジョブ側が書く**（job_record.py の所有者規約）。
        Neon 接続情報は Environment で渡す。
        """
        cfg = self._config
        attempt = self._next_attempt(Stage.TRAIN)
        job_env = telemetry_env()

        def call(ctx: RunContext) -> None:
            job_name = self._job_name(ctx.run_id)
            request: dict[str, Any] = {
                "TrainingJobName": job_name,
                "AlgorithmSpecification": {
                    "TrainingImage": cfg.training_image_uri,
                    "TrainingInputMode": "File",
                    # BYOC。イメージ既定の `train` ではなくシムを明示する
                    "ContainerEntrypoint": ["bash", ENTRYPOINT_PATH],
                },
                "RoleArn": cfg.execution_role_arn,
                "InputDataConfig": [
                    {
                        "ChannelName": TRAINING_CHANNEL,
                        "DataSource": {
                            "S3DataSource": {
                                "S3DataType": "S3Prefix",
                                "S3Uri": f"s3://{cfg.bucket}/{cfg.data_prefix}",
                                "S3DataDistributionType": "FullyReplicated",
                            }
                        },
                    }
                ],
                "OutputDataConfig": {"S3OutputPath": self.output_path()},
                "ResourceConfig": {
                    "InstanceType": cfg.instance_type,
                    "InstanceCount": cfg.instance_count,
                    "VolumeSizeInGB": cfg.volume_size_gb,
                },
                "StoppingCondition": {"MaxRuntimeInSeconds": cfg.max_runtime_seconds},
                # 値は文字列限定。型を保つため JSON 文字列を1キーに畳む
                "HyperParameters": {
                    PARAMS_HYPERPARAMETER_KEY: json.dumps(params),
                    RUN_ID_HYPERPARAMETER_KEY: ctx.run_id,
                    ATTEMPT_HYPERPARAMETER_KEY: str(attempt),
                },
                "Tags": [{"Key": k, "Value": v} for k, v in cfg.tags.items()],
            }
            if job_env:
                request["Environment"] = job_env
            if cfg.use_spot:
                request["EnableManagedSpotTraining"] = True
                request["StoppingCondition"]["MaxWaitTimeInSeconds"] = cfg.max_wait_seconds

            self.sagemaker.create_training_job(**request)
            self.training_job_name = job_name
            self._wait("training_job_completed_or_stopped", TrainingJobName=job_name)

            described = self.sagemaker.describe_training_job(TrainingJobName=job_name)
            job_status = described.get("TrainingJobStatus")
            if job_status != "Completed":
                reason = described.get("FailureReason", "(理由なし)")
                raise RuntimeError(f"Training job が {job_status} で終了: {reason}")

            ctx.params["training_job_name"] = job_name
            ctx.params["model_artifact_uri"] = described["ModelArtifacts"]["S3ModelArtifacts"]

        return self._tracked(
            Stage.TRAIN, dict(params), call, attempt=attempt, job_owns_success=True
        )

    def register_model(self, artifact_uri: str) -> MlRun:
        """Model Registry（Model Package Group）へ版を登録して承認する。

        グループ（器）は Terraform 側。ここは版の登録と承認だけ。
        承認まで自動でやるのは、比較の1ステップに人手の待ちを混ぜないため
        （承認フローの重さ自体は比較軸ではない。**承認ステップが存在すること**が
        Vertex との差であり、それは記録で表す）。
        """
        cfg = self._config

        def call(ctx: RunContext) -> None:
            response = self.sagemaker.create_model_package(
                ModelPackageGroupName=cfg.model_package_group_name,
                ModelPackageDescription=f"run_id={ctx.run_id}",
                InferenceSpecification={
                    "Containers": [{"Image": self._serving_image(), "ModelDataUrl": artifact_uri}],
                    "SupportedContentTypes": ["application/json"],
                    "SupportedResponseMIMETypes": ["application/json"],
                },
                ModelApprovalStatus="Approved",
            )
            self.model_package_arn = response["ModelPackageArn"]
            ctx.params["model_package_arn"] = self.model_package_arn
            ctx.params["approval_required"] = True

        return self._tracked(Stage.REGISTER, {"artifact_uri": artifact_uri}, call)

    def deploy(self, model_ref: str) -> MlRun:
        """Model → EndpointConfig → Endpoint を積み上げる。

        `model_ref` は成果物の S3 URI（model.tar.gz）。Vertex と違い
        **器だけ先に作れない**（Endpoint は EndpointConfig を、EndpointConfig は
        Model を、Model は学習成果物を要求する）。この非対称性が比較材料。

        ⚠️ Endpoint は常時課金。フェーズ末に必ず teardown() を呼ぶ。
        """
        cfg = self._config

        def call(ctx: RunContext) -> None:
            suffix = ctx.run_id.replace("-", "")[:12]
            model_name = f"{cfg.job_prefix}-model-{suffix}"
            endpoint_config_name = f"{cfg.job_prefix}-config-{suffix}"

            self.sagemaker.create_model(
                ModelName=model_name,
                PrimaryContainer={"Image": self._serving_image(), "ModelDataUrl": model_ref},
                ExecutionRoleArn=cfg.execution_role_arn,
                Tags=[{"Key": k, "Value": v} for k, v in cfg.tags.items()],
            )
            self.sagemaker.create_endpoint_config(
                EndpointConfigName=endpoint_config_name,
                ProductionVariants=[
                    {
                        "VariantName": "AllTraffic",
                        "ModelName": model_name,
                        "InitialInstanceCount": cfg.endpoint_instance_count,
                        "InstanceType": cfg.endpoint_instance_type,
                        "InitialVariantWeight": 1.0,
                    }
                ],
                Tags=[{"Key": k, "Value": v} for k, v in cfg.tags.items()],
            )

            # create の前に確定させる（後で見ると自分が作ったものを数えてしまう）
            reused = self._endpoint_exists()
            if reused:
                # 既存があるときは作り直さず設定を差し替える（停止時間が短く実運用の形）
                self.sagemaker.update_endpoint(
                    EndpointName=cfg.endpoint_name,
                    EndpointConfigName=endpoint_config_name,
                )
            else:
                self.sagemaker.create_endpoint(
                    EndpointName=cfg.endpoint_name,
                    EndpointConfigName=endpoint_config_name,
                    Tags=[{"Key": k, "Value": v} for k, v in cfg.tags.items()],
                )
            self._wait("endpoint_in_service", EndpointName=cfg.endpoint_name)

            self.model_name = model_name
            self.endpoint_config_name = endpoint_config_name
            ctx.params["model_name"] = model_name
            ctx.params["endpoint_config_name"] = endpoint_config_name
            ctx.params["endpoint_name"] = cfg.endpoint_name
            ctx.params["reused_endpoint"] = reused

        return self._tracked(Stage.DEPLOY, {"model_ref": model_ref}, call)

    def predict_one(self, instance: dict[str, Any]) -> MlRun:
        """/invocations を1回叩く（フェーズ完了条件⑤）。

        リクエスト形は3基盤共通（core/app/api/routes.py の PredictRequest）。
        """
        cfg = self._config

        def call(ctx: RunContext) -> None:
            response = self.runtime.invoke_endpoint(
                EndpointName=cfg.endpoint_name,
                ContentType="application/json",
                Accept="application/json",
                Body=json.dumps({"instances": [instance]}),
            )
            payload = json.loads(response["Body"].read())
            predictions = payload.get("predictions") if isinstance(payload, dict) else payload
            if not predictions:
                raise RuntimeError("predictions が空。serving コンテナの応答形式を確認")
            ctx.metrics["prediction"] = predictions[0]

        return self._tracked(Stage.PREDICT, {"instance": instance}, call)

    def teardown(self) -> MlRun:
        """Endpoint → EndpointConfig → Model の順に消す（依存の逆順）。

        逆順で消さないと「使用中」で失敗する。対象が無い場合も成功として記録する
        （撤退の冪等性）。**Model Package は消さない** = SageMaker 側の残留であり、
        比較レポートの残留リソース表に載る一次データ。
        """
        cfg = self._config

        def call(ctx: RunContext) -> None:
            removed: list[str] = []
            if self._endpoint_exists():
                self.sagemaker.delete_endpoint(EndpointName=cfg.endpoint_name)
                removed.append(f"endpoint:{cfg.endpoint_name}")
            if self.endpoint_config_name:
                self._delete_quietly(
                    self.sagemaker.delete_endpoint_config,
                    EndpointConfigName=self.endpoint_config_name,
                )
                removed.append(f"endpoint-config:{self.endpoint_config_name}")
                self.endpoint_config_name = None
            if self.model_name:
                self._delete_quietly(self.sagemaker.delete_model, ModelName=self.model_name)
                removed.append(f"model:{self.model_name}")
                self.model_name = None
            ctx.params["removed"] = removed
            ctx.params["residual_model_package"] = self.model_package_arn

        return self._tracked(Stage.TEARDOWN, {"action": "teardown"}, call)

    # --- 補助 -------------------------------------------------------------

    def output_path(self) -> str:
        return f"s3://{self._config.bucket}/{self._config.output_prefix}"

    def model_artifact_uri(self, train_run: MlRun) -> str:
        """train の MlRun から成果物 URI を取り出す（5基盤共通）。"""
        return artifact_uri_from(train_run)

    def _job_name(self, run_id: str) -> str:
        # SageMaker のジョブ名は英数字とハイフンのみ・63文字以内
        return f"{self._config.job_prefix}-train-{run_id.replace('-', '')[:12]}"

    def _serving_image(self) -> str:
        return self._config.serving_image_uri or self._config.training_image_uri

    def _endpoint_exists(self) -> bool:
        """存在確認。**確認できなかった場合は送出する**（失敗として記録させる）。

        権限不足を「無い」と扱うと teardown が嘘の成功を返し、
        課金が続いたまま残留検査にも上がらない。
        """
        try:
            self.sagemaker.describe_endpoint(EndpointName=self._config.endpoint_name)
        except Exception as exc:
            if is_missing(exc):
                return False
            raise
        return True

    def _wait(self, waiter_name: str, **kwargs: Any) -> None:
        """完了待ち。waiter を持たない代役クライアント（テスト）では何もしない。"""
        getter = getattr(self.sagemaker, "get_waiter", None)
        if getter is None:
            return
        getter(waiter_name).wait(**kwargs)

    @staticmethod
    def _delete_quietly(delete: Callable[..., Any], **kwargs: Any) -> None:
        """既に無いものを消そうとしても teardown 全体を失敗させない。"""
        try:
            delete(**kwargs)
        except Exception:  # noqa: BLE001
            _logger.info("削除対象が見つからない: %s", kwargs)
