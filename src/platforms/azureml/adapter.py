"""Azure ML adapter。

Terraform でカバー: Resource Group / Storage / ACR / Key Vault /
                    App Insights / Workspace / Compute Cluster
SDK に残る      : Command Job / Model 登録 / Managed Online Endpoint 更新

実行契約: command job の inputs / outputs マウントパス（引数は自由）。推論は /score。
entrypoint シムは docker/training/entrypoint_azureml.sh。

移植元: ローカル資産にゼロ（流用元なし）。
TMP/azureml-examples の CLI v2 Command Job YAML を Python SDK v2（azure-ai-ml）へ
読み替えた新規実装。Vertex / SageMaker adapter と同じ形に揃えてある。

構造仮説（レポートで実測検証する）: **周辺依存で初期構築量が最大**。
Workspace は storage / key_vault / app_insights を必須で要求する
（実測済み: azurerm v4.81.0。ACR は任意だが BYOC には要る）。

⚠️ サブスクリプションは FreeTrial 枠。vCPU quota が低く、他基盤と同一マシンサイズを
取れない可能性がある。取れなかった事実自体を記録する（比較の前提として重要）。

他2基盤との差（比較レポート行き）:
  - 学習の入出力が **ジョブ定義側のマウント宣言**（`${{inputs.x}}` / `${{outputs.y}}`）。
    Vertex は環境変数（AIP_MODEL_DIR）、SageMaker は固定パス（/opt/ml）。3者3様。
  - 推論は **エンドポイント + デプロイの2階層**で、トラフィック配分が別操作。
  - 1件推論が **ファイル渡し**（invoke の request_file）。他2基盤は文字列/辞書で渡せる。
"""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.telemetry.schemas import MlRun, Platform, Stage, Tier, UnificationUnit
from core.telemetry.tracking import RunContext, RunSink
from platforms.shared.contracts.tracking import (
    TrackedOperations,
    artifact_uri_from,
    is_missing,
    telemetry_env,
)

_logger = logging.getLogger("platforms.azureml")

# 学習イメージ内でのシムの配置。docker/training/Dockerfile の COPY 先と一致させる。
ENTRYPOINT_PATH = "/app/entrypoint_azureml.sh"

# 推論コンテナの契約（core/app/api/routes.py の Azure ML 用ルート）
SCORING_ROUTE = "/score"
LIVENESS_ROUTE = "/health"
SERVING_PORT = 8080


@dataclass(frozen=True)
class AzureMlConfig:
    """Terraform の outputs（infra/environments/azure-dev）から埋める。"""

    subscription_id: str
    resource_group: str
    workspace_name: str
    compute_cluster: str
    training_image_uri: str
    serving_image_uri: str | None = None
    # Spot 相当（LowPriority）を使うか。**adapter はこの値で挙動を変えない。**
    # Azure だけ Spot が compute cluster の属性（`vm_priority`）で、ジョブ側の
    # 指定ではないため、実際に効かせるのは Terraform（`compute_cluster_vm_priority`）。
    # ここに持つのは (1) common.use_spot が黙って捨てられるのを防ぐため、
    # (2) Terraform 変数の供給元を config.yaml に一本化するため（修正04）。
    use_spot: bool = True
    # 学習データ。既定は Workspace 既定データストアの相対パス
    data_uri: str = "azureml://datastores/workspaceblobstore/paths/data/california_housing"
    model_name: str = "mcml-california-housing"
    endpoint_name: str = "mcml-dev-endpoint"
    deployment_name: str = "primary"
    endpoint_instance_type: str = "Standard_DS2_v2"
    endpoint_instance_count: int = 1
    # Azure ML の実験は**ジョブ submit の必須概念**（省略すると既定名が使われる）。
    # Vertex（事後 API）でも SageMaker（任意の投入パラメータ）でもなく、
    # **常に何らかの実験に属する**のが Azure の制約。したがってここだけ既定値を持ち、
    # 「無効化」という状態が存在しない。この非対称は吸収せず記録する
    # （docs/02_architecture.md「設計の導出順序」手順3）。
    # 名前は `experiment` に揃えた（3基盤で同じフィールド名 = 同じ env 規約
    # MCML_<PLATFORM>_EXPERIMENT で上書きできる）。
    experiment: str = "multicloud-ml-platform-lab"
    tags: dict[str, str] = field(default_factory=lambda: {"project": "multicloud-ml-platform-lab"})


class _Entities:
    """azure.ai.ml のエンティティ生成をまとめた遅延 import 用の窓口。

    SDK 未インストール環境でも adapter を import でき、テストでは偽物を
    差し込めるようにするため、生成関数をここへ集約する。
    """

    def __init__(self) -> None:
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        from azure.ai.ml import Input, Output, command  # noqa: PLC0415
        from azure.ai.ml.entities import (  # noqa: PLC0415
            Environment,
            ManagedOnlineDeployment,
            ManagedOnlineEndpoint,
            Model,
        )

        self.__dict__.update(
            command=command,
            Input=Input,
            Output=Output,
            Environment=Environment,
            Model=Model,
            ManagedOnlineEndpoint=ManagedOnlineEndpoint,
            ManagedOnlineDeployment=ManagedOnlineDeployment,
            _loaded=True,
        )

    def __getattr__(self, name: str) -> Any:
        self._load()
        return self.__dict__[name]


class AzureMLAdapter(TrackedOperations):
    """Golden Path ステップ2〜3 の Azure ML 実装。

    使い方:

        adapter = AzureMLAdapter(config, sink=sink)
        train = adapter.submit_training({"num_leaves": 31})
        adapter.register_model(adapter.model_artifact_uri(train))
        adapter.deploy(adapter.model_resource_name)
        adapter.predict_one({...})
        adapter.teardown()   # Managed Online Endpoint は常時課金
    """

    platform = Platform.AZUREML
    tier = Tier.A
    unification_unit = UnificationUnit.CONTAINER

    def __init__(
        self,
        config: AzureMlConfig,
        *,
        sink: RunSink,
        ml_client: Any | None = None,
        entities: Any | None = None,
    ) -> None:
        self._config = config
        self._sink = sink
        self._ml_client = ml_client
        self._entities = entities if entities is not None else _Entities()
        self.job_name: str | None = None
        self.model_resource_name: str | None = None
        self.endpoint_scoring_uri: str | None = None

    # --- SDK 解決（遅延 import）-------------------------------------------

    @property
    def client(self) -> Any:
        if self._ml_client is None:
            from azure.ai.ml import MLClient  # noqa: PLC0415
            from azure.identity import DefaultAzureCredential  # noqa: PLC0415

            self._ml_client = MLClient(
                credential=DefaultAzureCredential(),
                subscription_id=self._config.subscription_id,
                resource_group_name=self._config.resource_group,
                workspace_name=self._config.workspace_name,
            )
        return self._ml_client

    @property
    def entities(self) -> Any:
        return self._entities

    # --- PlatformAdapter -------------------------------------------------

    def training_job(
        self, run_id: str, attempt: int, params_json: str, job_env: dict[str, str]
    ) -> Any:
        """学習用の command job を組み立てる（**投入はしない**）。

        切り出してあるのは Azure ML Pipelines が**同じ job をそのまま**ステップとして
        合成できるため（`platforms/azureml/pipeline.py`）。Vertex と違い
        ステップは「コンテナを動かす」のではなく「command job を並べる」ので、
        オーケストレータ用の別イメージが要らない —— この差が Vertex 側で
        パイプライン化を見送った理由そのもの（修正11）。

        投入経路とパイプライン経路で**同じ関数**を使うことが要点。別々に組むと
        「CLI では通るがパイプラインでは落ちる」差が生まれ、比較が濁る。
        """
        cfg = self._config
        return self.entities.command(
            display_name=f"{cfg.model_name}-train",
            experiment_name=cfg.experiment,
            compute=cfg.compute_cluster,
            environment=self.entities.Environment(image=cfg.training_image_uri),
            environment_variables=job_env,
            inputs={"data": self.entities.Input(type="uri_folder", path=cfg.data_uri)},
            outputs={"model": self.entities.Output(type="uri_folder")},
            command=(
                f"bash {ENTRYPOINT_PATH} "
                "--input ${{inputs.data}} "
                "--output ${{outputs.model}} "
                f"--params '{params_json}' "
                f"--run-id {run_id} "
                f"--attempt {attempt}"
            ),
            tags=cfg.tags,
        )

    def submit_training(self, params: dict[str, Any]) -> MlRun:
        """Command Job を投入し、完了まで待つ。

        入出力はジョブ定義側のマウント宣言（`${{inputs.data}}` / `${{outputs.model}}`）で
        解決される。シムはそれを引数として受け取るだけ。

        run_id / attempt を引数で渡し、**成功時の ml_runs 行はジョブ側が書く**
        （job_record.py の所有者規約）。Neon 接続情報は environment_variables で渡す。
        """
        params_json = json.dumps(params)
        attempt = self._next_attempt(Stage.TRAIN)
        job_env = telemetry_env()

        def call(ctx: RunContext) -> None:
            job = self.training_job(ctx.run_id, attempt, params_json, job_env)
            created = self.client.jobs.create_or_update(job)
            self.job_name = created.name
            # stream() は完了までブロックする（失敗時は例外）
            self.client.jobs.stream(created.name)

            finished = self.client.jobs.get(created.name)
            status = getattr(finished, "status", None)
            if status not in {"Completed", None}:
                raise RuntimeError(f"Command Job が {status} で終了: {created.name}")

            ctx.params["job_name"] = created.name
            ctx.params["model_artifact_uri"] = self.model_artifact_uri_for(created.name)

        return self._tracked(
            Stage.TRAIN, dict(params), call, attempt=attempt, job_owns_success=True
        )

    def register_model(self, artifact_uri: str) -> MlRun:
        """ジョブ出力を Model として登録する（`azureml://jobs/.../outputs/model`）。

        Vertex の版エイリアスや SageMaker の承認に相当するものは無く、
        **名前 + 自動採番バージョン**だけ。この軽さも比較材料。
        """
        cfg = self._config

        def call(ctx: RunContext) -> None:
            model = self.client.models.create_or_update(
                self.entities.Model(
                    path=artifact_uri,
                    name=cfg.model_name,
                    type="custom_model",
                    description=f"run_id={ctx.run_id}",
                    tags=cfg.tags,
                )
            )
            version = getattr(model, "version", None)
            self.model_resource_name = f"{cfg.model_name}:{version}" if version else cfg.model_name
            ctx.params["model_resource_name"] = self.model_resource_name

        return self._tracked(Stage.REGISTER, {"artifact_uri": artifact_uri}, call)

    def deploy(self, model_ref: str) -> MlRun:
        """Managed Online Endpoint + Deployment を作り、トラフィックを 100% 寄せる。

        **エンドポイントとデプロイの2階層**で、トラフィック配分は別操作。
        BYOC なので Environment に推論ルート（/score・liveness）を宣言する。

        ⚠️ 常時課金。フェーズ末に必ず teardown() を呼ぶ。
        """
        cfg = self._config

        def call(ctx: RunContext) -> None:
            endpoint = self.entities.ManagedOnlineEndpoint(
                name=cfg.endpoint_name,
                auth_mode="key",
                tags=cfg.tags,
            )
            self.client.online_endpoints.begin_create_or_update(endpoint).result()

            environment = self.entities.Environment(
                image=cfg.serving_image_uri or cfg.training_image_uri,
                inference_config={
                    "liveness_route": {"path": LIVENESS_ROUTE, "port": SERVING_PORT},
                    "readiness_route": {"path": LIVENESS_ROUTE, "port": SERVING_PORT},
                    "scoring_route": {"path": SCORING_ROUTE, "port": SERVING_PORT},
                },
            )
            deployment = self.entities.ManagedOnlineDeployment(
                name=cfg.deployment_name,
                endpoint_name=cfg.endpoint_name,
                model=model_ref,
                environment=environment,
                instance_type=cfg.endpoint_instance_type,
                instance_count=cfg.endpoint_instance_count,
                tags=cfg.tags,
            )
            self.client.online_deployments.begin_create_or_update(deployment).result()

            # トラフィック配分は endpoint 側の更新。ここを忘れると 0% のまま呼べない
            endpoint.traffic = {cfg.deployment_name: 100}
            updated = self.client.online_endpoints.begin_create_or_update(endpoint).result()
            self.endpoint_scoring_uri = getattr(updated, "scoring_uri", None)

            ctx.params["endpoint_name"] = cfg.endpoint_name
            ctx.params["deployment_name"] = cfg.deployment_name
            ctx.params["traffic"] = {cfg.deployment_name: 100}

        return self._tracked(Stage.DEPLOY, {"model_ref": model_ref}, call)

    def predict_one(self, instance: dict[str, Any]) -> MlRun:
        """/score を1回叩く（フェーズ完了条件⑤）。

        Azure SDK の invoke は **ファイル渡し**しか受けないので一時ファイルを作る
        （他2基盤は文字列/辞書で渡せる。この差も記録対象）。
        """
        cfg = self._config

        def call(ctx: RunContext) -> None:
            payload = {"instances": [instance]}
            with tempfile.TemporaryDirectory() as tmp:
                request_file = Path(tmp) / "request.json"
                request_file.write_text(json.dumps(payload), encoding="utf-8")
                response = self.client.online_endpoints.invoke(
                    endpoint_name=cfg.endpoint_name,
                    deployment_name=cfg.deployment_name,
                    request_file=str(request_file),
                )
            parsed = json.loads(response) if isinstance(response, str) else response
            predictions = parsed.get("predictions") if isinstance(parsed, dict) else parsed
            if not predictions:
                raise RuntimeError("predictions が空。serving コンテナの応答形式を確認")
            ctx.metrics["prediction"] = predictions[0]

        return self._tracked(Stage.PREDICT, {"instance": instance}, call)

    def teardown(self) -> MlRun:
        """Endpoint を消す（配下の Deployment も一緒に消える）。

        対象が無い場合も成功として記録する（撤退の冪等性）。
        登録済み Model は残る = Azure 側の残留として比較表に載る。
        """
        cfg = self._config

        def call(ctx: RunContext) -> None:
            removed: list[str] = []
            if self._endpoint_exists():
                self.client.online_endpoints.begin_delete(name=cfg.endpoint_name).result()
                removed.append(f"endpoint:{cfg.endpoint_name}")
            self.endpoint_scoring_uri = None
            ctx.params["removed"] = removed
            ctx.params["residual_model"] = self.model_resource_name

        return self._tracked(Stage.TEARDOWN, {"action": "teardown"}, call)

    # --- 補助 -------------------------------------------------------------

    def model_artifact_uri_for(self, job_name: str) -> str:
        """ジョブ出力の参照。Model 登録時の path に渡す形。"""
        return f"azureml://jobs/{job_name}/outputs/model"

    def model_artifact_uri(self, train_run: MlRun) -> str:
        """train の MlRun から成果物 URI を取り出す（5基盤共通）。"""
        return artifact_uri_from(train_run)

    def _endpoint_exists(self) -> bool:
        """存在確認。確認できなかった場合は送出する（失敗として記録させる）。"""
        try:
            self.client.online_endpoints.get(name=self._config.endpoint_name)
        except Exception as exc:
            if is_missing(exc):
                return False
            raise
        return True
