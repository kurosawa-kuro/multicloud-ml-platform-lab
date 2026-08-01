"""Databricks adapter。

Tier B なので統一単位はコンテナではなく **src/core/ml を wheel 化したパッケージ**。

    投入   : Jobs API / Python wheel task（serverless）
    追跡   : MLflow（基盤内蔵。本ラボの一次記録は Neon ml_runs）
    登録   : Unity Catalog Models（catalog.schema.model の3階層名前空間）
    推論   : Mosaic AI Model Serving（scale-to-zero 可）

Terraform でカバー: Catalog / Schema / Grants / Cluster・Policy / Job /
                    Registered Model / Serving Endpoint
SDK に残る      : ジョブ実行トリガ / MLflow run / モデルバージョン昇格

移植元: ML/study-snowflake-databricks/src/databricks_job_trigger.py（Jobs API
`run-now` の形）+ TMP/terraform-provider-databricks の docs（serving の
entity_name + entity_version + workload_size + scale_to_zero の組）。
流用元: kaggle-bronze-gcp の Databricks 実行経路。

**ジョブ定義は Terraform が持ち、adapter は起動するだけ**（境界の原則）。
`jobs/create` を adapter 側でやると、Terraform 網羅度の比較が壊れる
（「Terraform でどこまで書けたか」が比較軸そのもの）。

Tier A 3基盤との差（比較レポート行き）:
  - 実行単位が **wheel + entry point**。コンテナイメージが登場しない。
  - モデルの所在が **カタログの中**（catalog.schema.model）。独立したレジストリではない。
  - 推論エンドポイントが **scale-to-zero** 可。Tier A の常時課金と構造が違う。
  - 実験追跡が基盤内蔵（MLflow）。ただし比較の一次記録は Neon 側に寄せる。

実装上の注意:
  - serverless の python_wheel_task は environment_key が必須（Terraform 側で設定済み）
  - ML Runtime プリインストール版と wheel 依存が衝突しうる（依存最小の理由）
  - Neon への到達は serverless egress 設定を経由する。到達不能は
    failure_class='network' で記録し、JSONL fallback へ落とす
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.telemetry.schemas import MlRun, Platform, Stage, Tier, UnificationUnit
from core.telemetry.tracking import RunContext, RunSink
from platforms.shared.contracts.packaging import require_code_revision_stamp
from platforms.shared.contracts.tracking import TrackedOperations, artifact_uri_from, is_missing
from platforms.shared.packaging_names import wheel_filename as wheel_filename_from_pyproject

_logger = logging.getLogger("platforms.databricks")

# 実行の終端状態（Jobs API 2.1 の result_state）
TERMINAL_SUCCESS = "SUCCESS"


@dataclass(frozen=True)
class DatabricksConfig:
    """Terraform の outputs（infra/environments/dbx-dev）から埋める。

    host / token は環境変数（DATABRICKS_HOST / DATABRICKS_TOKEN）で SDK が拾うので
    ここには持たない（秘密をこのオブジェクトに載せない）。
    """

    catalog: str
    schema: str
    volume: str = "artifacts"
    model_name: str = "california_housing"
    job_name: str = "mcml_dev_train"
    serving_endpoint_name: str = "mcml_dev_serving"
    serving_workload_size: str = "Small"
    scale_to_zero: bool = True
    # MLflow の実験名（登録ジョブが run を置く場所）。wheel task には既定の実験が
    # 無く、指定しないと登録が `No experiment was found` で落ちる。
    experiment_name: str = "mcml-lab"
    data_prefix: str = "data/california_housing"
    # `python -m build` が作る実 dist 名。**pyproject の name/version から導出する**
    # （python_wheel_task は dist 名から entry point を引く。ずれると起動しない）。
    # 手書きしていた頃は config.yaml / adapter / Terraform の3箇所に版が散っていた。
    wheel_filename: str = field(default_factory=wheel_filename_from_pyproject)
    tags: dict[str, str] = field(default_factory=lambda: {"project": "multicloud-ml-platform-lab"})

    @property
    def model_full_name(self) -> str:
        """UC の3階層名前空間。モデルがカタログの中に居ることがそのまま名前に出る。"""
        return f"{self.catalog}.{self.schema}.{self.model_name}"

    @property
    def volume_root(self) -> str:
        return f"/Volumes/{self.catalog}/{self.schema}/{self.volume}"

    @property
    def wheel_path(self) -> str:
        return f"{self.volume_root}/dist/{self.wheel_filename}"

    @property
    def data_path(self) -> str:
        """学習入力（Parquet）の置き場。Volume は通常のファイルパスとして読める。"""
        return f"{self.volume_root}/{self.data_prefix}"

    def output_path(self, run_id: str) -> str:
        """成果物の出力先。**run_id 基準**（Databricks 側の run_id は完了後にしか
        分からないので、それを成果物パスに使うとジョブ側が書けない）。"""
        return f"{self.volume_root}/runs/{run_id}/model"


class DatabricksAdapter(TrackedOperations):
    """Golden Path ステップ2〜3 の Databricks 実装。

    使い方:

        adapter = DatabricksAdapter(config, sink=sink)
        wheel = adapter.build_wheel(Path("."), Path("artifacts/dist"))
        adapter.upload_wheel(wheel)
        train = adapter.submit_training({"num_leaves": 31})
        adapter.register_model(adapter.model_artifact_uri(train))
        adapter.deploy(adapter.model_version)
        adapter.predict_one({...})
        adapter.teardown()   # serving は scale-to-zero でもエンドポイント定義は残る
    """

    platform = Platform.DATABRICKS
    tier = Tier.B
    unification_unit = UnificationUnit.PACKAGE

    def __init__(
        self,
        config: DatabricksConfig,
        *,
        sink: RunSink,
        workspace_client: Any | None = None,
        entities: Any | None = None,
    ) -> None:
        self._config = config
        self._client = workspace_client
        self._entities = entities if entities is not None else _Entities()
        self._sink = sink
        self.job_id: int | None = None
        self.run_id_remote: int | None = None
        self.model_version: str | None = None

    # --- SDK 解決（遅延 import）-------------------------------------------

    @property
    def client(self) -> Any:
        if self._client is None:
            from databricks.sdk import WorkspaceClient  # noqa: PLC0415 - 遅延 import は意図的

            self._client = WorkspaceClient()
        return self._client

    @property
    def entities(self) -> Any:
        return self._entities

    # --- wheel（Tier B の統一単位）---------------------------------------

    def build_wheel(self, source_dir: Path, output_dir: Path) -> Path:
        """`src/core/ml` を含む wheel をビルドする。Tier B の同一性担保はこの成果物。

        ビルドは `python -m build --wheel` に委譲する（自前で zip を組まない。
        メタデータの差で「同じパッケージのはず」が崩れると比較が壊れる）。

        **stamp が焼かれていることを先に確かめる。** wheel の中には .git も
        CODE_REVISION 環境変数も無いため、`make stamp-revision` を忘れると
        ジョブが起動してから CodeRevisionError で落ちる（実行時間を捨てる上に、
        原因が「Databricks の問題」に見えてしまう）。
        """
        require_code_revision_stamp(source_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(  # noqa: S603 - 引数は固定
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(output_dir)],
            cwd=str(source_dir),
            check=True,
            capture_output=True,
            text=True,
        )
        wheels = sorted(output_dir.glob("*.whl"))
        if not wheels:
            raise RuntimeError(f"wheel が生成されなかった: {output_dir}")
        return wheels[-1]

    def upload_wheel(self, wheel: Path) -> str:
        """UC Volume へ wheel を置く。ジョブの environment.dependencies が読む場所。"""
        remote = self._config.wheel_path
        with wheel.open("rb") as f:
            self.client.files.upload(remote, f, overwrite=True)
        return remote

    def upload_dataset(self, dataset: Path) -> str:
        """学習入力の Parquet を Volume へ置く（Tier A の GCS / S3 配布に相当）。

        置き先は `data_path` 配下。`read_parquet` はディレクトリを受けて中の
        Parquet 1本を読むので、**ファイル名は問わないが1本だけ**にする。
        """
        remote = f"{self._config.data_path}/{dataset.name}"
        with dataset.open("rb") as f:
            self.client.files.upload(remote, f, overwrite=True)
        return remote

    # --- PlatformAdapter -------------------------------------------------

    def submit_training(self, params: dict[str, Any]) -> MlRun:
        """Terraform が定義した Job を起動して完了を待つ。

        ジョブ定義（wheel task / environment_key / serverless）は Terraform 側。
        ここは **起動と結果判定だけ**。

        python_params は wheel の entry point（`train` = platforms.databricks.job_main）へ
        そのまま渡る。Tier A のシムと同じ引数の形（`--input` / `--output` が無いと
        CLI が exit 2 で即死する）。入出力は UC Volume の通常パス。
        run_id / attempt を渡すのは、**成功時の ml_runs 行を job_main が書く**ため
        （job_record.py の所有者規約）。

        ⚠️ Neon 接続情報をジョブへ渡す経路は未配線（serverless の env は Terraform の
        environment.spec に無く、Databricks secret 参照の配線は Phase 3 の precheck 項目）。
        現状ジョブ側は Volume 上の JSONL fallback に落ちる = write_path='collected'。
        **その事実自体が Tier B の到達経路の比較データ**なので、握り潰さず記録する。
        """
        cfg = self._config
        attempt = self._next_attempt(Stage.TRAIN)

        def call(ctx: RunContext) -> None:
            job_id = self._resolve_job_id()
            output_path = cfg.output_path(ctx.run_id)
            waiter = self.client.jobs.run_now(
                job_id=job_id,
                python_params=[
                    "--input",
                    cfg.data_path,
                    "--output",
                    output_path,
                    "--params",
                    json.dumps(params),
                    "--run-id",
                    ctx.run_id,
                    "--attempt",
                    str(attempt),
                ],
            )
            run = waiter.result() if hasattr(waiter, "result") else waiter
            self.run_id_remote = getattr(run, "run_id", None)

            state = getattr(run, "state", None)
            result_state = getattr(state, "result_state", None)
            # SDK は enum を返すので value を優先して文字列化する
            result_text = str(getattr(result_state, "value", result_state))
            if result_state is not None and result_text != TERMINAL_SUCCESS:
                message = getattr(state, "state_message", "")
                raise RuntimeError(f"Job run が {result_text} で終了: {message}")

            ctx.params["job_id"] = job_id
            ctx.params["remote_run_id"] = self.run_id_remote
            ctx.params["model_artifact_uri"] = output_path

        return self._tracked(
            Stage.TRAIN, dict(params), call, attempt=attempt, job_owns_success=True
        )

    def register_model(self, artifact_uri: str) -> MlRun:
        """Unity Catalog Models へ版を登録する。**ジョブを1本起こして行う**。

        登録先は `catalog.schema.model`。**モデルがデータと同じガバナンス面に乗る**のが
        Tier A（独立レジストリ）との構造差。器（registered model）は Terraform 側。

        ⚠️ **手元から SDK で登録する経路が存在しない。**
        `w.model_versions` は get/list/update/delete のみで版を作れず、公式も
        「Creating new model versions requires use of the MLflow Python client」と明記する
        （2026-08-01 実測 + 参照）。MLflow クライアントはワークスペース内で動くのが前提なので、
        **登録も Terraform 定義のジョブを `--stage register` で起こして実行する**。

        結果として Databricks の④は「API 1本」ではなく**ジョブ起動のオーバーヘッドを伴う**。
        これは実装の都合ではなく基盤の構造なので、所要時間をそのまま比較表に載せる。
        版番号は戻り値を信じず **UC を引き直して確定**する（ジョブの stdout に依存しない）。
        """
        cfg = self._config

        def call(ctx: RunContext) -> None:
            job_id = self._resolve_job_id()
            waiter = self.client.jobs.run_now(
                job_id=job_id,
                python_params=[
                    "--stage",
                    "register",
                    "--output",
                    artifact_uri,
                    "--model-name",
                    cfg.model_full_name,
                    "--run-id",
                    ctx.run_id,
                    "--experiment",
                    self._experiment_path(),
                ],
            )
            run = waiter.result() if hasattr(waiter, "result") else waiter

            state = getattr(run, "state", None)
            result_state = getattr(state, "result_state", None)
            result_text = str(getattr(result_state, "value", result_state))
            if result_state is not None and result_text != TERMINAL_SUCCESS:
                message = getattr(state, "state_message", "")
                raise RuntimeError(f"登録ジョブが {result_text} で終了: {message}")

            self.model_version = self._latest_model_version()
            if self.model_version is None:
                raise RuntimeError(f"UC に版が見つからない: {cfg.model_full_name}")
            ctx.params["model_full_name"] = cfg.model_full_name
            ctx.params["model_version"] = self.model_version
            ctx.params["registered_via"] = "mlflow-in-job"

        return self._tracked(Stage.REGISTER, {"artifact_uri": artifact_uri}, call)

    def deploy(self, model_ref: str) -> MlRun:
        """Mosaic AI Model Serving を立てる。`model_ref` はモデルバージョン。

        **scale_to_zero_enabled を必ず立てる**（Tier A の常時課金との構造差であり、
        アイドル課金比較の核心。落とすと比較の前提が変わる）。
        """
        cfg = self._config

        def call(ctx: RunContext) -> None:
            served = self.entities.ServedEntityInput(
                name="current",
                entity_name=cfg.model_full_name,
                entity_version=str(model_ref),
                workload_size=cfg.serving_workload_size,
                scale_to_zero_enabled=cfg.scale_to_zero,
            )
            # `name` は必須（SDK 0.123 実測）。create 側で name を二重に渡す形になるが、
            # 省くと TypeError で **エンドポイントを1つも作れない**。
            endpoint_config = self.entities.EndpointCoreConfigInput(
                name=cfg.serving_endpoint_name, served_entities=[served]
            )

            if self._endpoint_exists():
                self.client.serving_endpoints.update_config_and_wait(
                    name=cfg.serving_endpoint_name,
                    served_entities=[served],
                )
                reused = True
            else:
                self.client.serving_endpoints.create_and_wait(
                    name=cfg.serving_endpoint_name,
                    config=endpoint_config,
                )
                reused = False

            ctx.params["serving_endpoint"] = cfg.serving_endpoint_name
            ctx.params["entity_version"] = str(model_ref)
            ctx.params["scale_to_zero"] = cfg.scale_to_zero
            ctx.params["reused_endpoint"] = reused

        return self._tracked(Stage.DEPLOY, {"model_ref": model_ref}, call)

    def predict_one(self, instance: dict[str, Any]) -> MlRun:
        """serving エンドポイントへ1件投げる（フェーズ完了条件⑤）。

        Tier A と違い **自前の推論コンテナを経由しない**（基盤が直接モデルを配信する）。
        入力の渡し方も dataframe_records で、3契約の HTTP 形とは別物。
        """
        cfg = self._config

        def call(ctx: RunContext) -> None:
            response = self.client.serving_endpoints.query(
                name=cfg.serving_endpoint_name,
                dataframe_records=[instance],
            )
            predictions = getattr(response, "predictions", None)
            if not predictions:
                raise RuntimeError("predictions が空。serving エンドポイントの状態を確認")
            ctx.metrics["prediction"] = predictions[0]

        return self._tracked(Stage.PREDICT, {"instance": instance}, call)

    def teardown(self) -> MlRun:
        """serving エンドポイントと **UC のモデル版**を削除する。

        scale-to-zero でも **エンドポイント定義自体は残る**ので、フェーズ末に消す。

        モデル版まで消すのは、**残しておくと terraform destroy が止まる**ため
        （2026-08-01 実測: `cannot delete registered model: ... has 2 model versions(s)`。
        `databricks_registered_model` に force_destroy が無い）。
        版を作るのは SDK/MLflow 側なので、**作った層が片付ける**。
        Vertex は登録モデルが残っても destroy が通る = ここは Tier B 固有の非対称。
        """
        cfg = self._config

        def call(ctx: RunContext) -> None:
            removed: list[str] = []
            if self._endpoint_exists():
                self.client.serving_endpoints.delete(name=cfg.serving_endpoint_name)
                removed.append(f"serving-endpoint:{cfg.serving_endpoint_name}")
            for version in self._model_versions():
                self.client.model_versions.delete(
                    full_name=cfg.model_full_name, version=int(version)
                )
                removed.append(f"model-version:{cfg.model_full_name}:{version}")
            ctx.params["removed"] = removed
            ctx.params["residual_model_version"] = None
            ctx.params["residual_wheel"] = cfg.wheel_path

        return self._tracked(Stage.TEARDOWN, {"action": "teardown"}, call)

    # --- 補助 -------------------------------------------------------------

    def model_artifact_uri_for(self, run_id: str) -> str:
        """学習ジョブが Volume へ書いた成果物の場所（ml_runs.run_id 基準）。"""
        return self._config.output_path(run_id)

    def model_artifact_uri(self, train_run: MlRun) -> str:
        """train の MlRun から成果物 URI を取り出す（5基盤共通）。"""
        return artifact_uri_from(train_run)

    def _resolve_job_id(self) -> int:
        """ジョブ名から ID を引く。ID を設定に手書きすると apply のたびに腐る。"""
        if self.job_id is not None:
            return self.job_id
        for job in self.client.jobs.list(name=self._config.job_name):
            job_id = getattr(job, "job_id", None)
            if job_id is not None:
                self.job_id = int(job_id)
                return self.job_id
        raise RuntimeError(
            f"Job が見つからない（name={self._config.job_name}）。terraform apply 済みか確認"
        )

    def _experiment_path(self) -> str:
        """MLflow 実験のワークスペースパス。

        Free Edition は `/Shared` を持たないことがあるので、**実行ユーザーのホーム**を既定にする。
        解決できなければ `/Shared` へ落とす（失敗はジョブ側の失敗として記録される）。
        """
        user = getattr(self.client.current_user.me(), "user_name", None)
        return (
            f"/Users/{user}/{self._config.experiment_name}"
            if user
            else (f"/Shared/{self._config.experiment_name}")
        )

    def _model_versions(self) -> list[int]:
        """UC に実在する版番号（無い / 引けない場合は空）。"""
        try:
            listed = self.client.model_versions.list(self._config.model_full_name)
        except Exception as exc:
            if is_missing(exc):
                return []
            raise
        return [int(v) for v in (getattr(i, "version", None) for i in listed) if v is not None]

    def _latest_model_version(self) -> str | None:
        """UC に実在する最新版を引く（登録ジョブの出力を信用しない）。"""
        versions = self._model_versions()
        return str(max(versions)) if versions else None

    def _endpoint_exists(self) -> bool:
        """存在確認。確認できなかった場合は送出する（失敗として記録させる）。"""
        try:
            self.client.serving_endpoints.get(name=self._config.serving_endpoint_name)
        except Exception as exc:
            if is_missing(exc):
                return False
            raise
        return True


class _Entities:
    """databricks.sdk.service.serving の入力型をまとめた遅延 import 用の窓口。"""

    def __init__(self) -> None:
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        from databricks.sdk.service.serving import (  # noqa: PLC0415
            EndpointCoreConfigInput,
            ServedEntityInput,
        )

        self.__dict__.update(
            EndpointCoreConfigInput=EndpointCoreConfigInput,
            ServedEntityInput=ServedEntityInput,
            _loaded=True,
        )

    def __getattr__(self, name: str) -> Any:
        self._load()
        return self.__dict__[name]
