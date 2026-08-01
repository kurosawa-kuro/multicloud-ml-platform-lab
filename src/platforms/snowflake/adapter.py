"""Snowflake adapter。

Tier B。統一単位は **src/core/ml を stage へアップロードして import** する形。

    投入   : Snowpark Python stored procedure（warehouse 実行）
    追跡   : 内蔵の実験追跡は限定的 → Neon 側の ml_runs を一次記録とする
    登録   : Snowflake Model Registry（スキーマ内オブジェクト）
    推論   : Model Registry 経由の SQL 関数（SPCS は差分メモ止まり・主経路にしない）

Terraform でカバー: Database / Schema / Warehouse / Role / Grants / Stage /
                    Network Rule / Secret / External Access Integration
SDK・SQL に残る  : Stored Procedure 実行 / Model Registry 登録 / サービス関数作成

移植元: ML/eks-app-mlops-platform-v2/mlops/src/adapters/snowflake_adapter.py（接続の型）+
TMP/snowflake-ml-example-project（外部オーケストレータ実行方式）+
TMP/snowflake-ml-python（Registry の API を実物で確認: `Registry(session, database_name=,
schema_name=)` / `log_model(model, model_name=, version_name=, comment=, metrics=)` /
`ModelVersion.run(X, function_name=)` / `Model.default` setter）。
流用元: gcp-search-mlops-gke の Snowflake 検証。

**発見（比較レポート行き）: Snowflake には「デプロイ」に相当するリソースが無い。**
モデルを登録した時点で warehouse から SQL で呼べるため、Tier A の
「エンドポイントを立てる → 常時課金 → teardown」というライフサイクルが存在しない。
`deploy()` は既定バージョンの切り替えだけで、作られるインフラは無い。
アイドル課金の構造差としては5基盤で最も軽い側の極。

実装上の注意:
  - warehouse Python は Anaconda channel 限定でバージョン固定が効きにくい
    （依存最小の理由）。sproc の PACKAGES は channel にある名前で書く。
  - Neon への到達は network rule + secret + external access integration を経由する。
    到達不能は failure_class='network' で記録し JSONL fallback（sproc から stage へ）。
  - 残留候補: Time Travel / Fail-safe / カタログ内オブジェクト / stage 成果物。
    **Fail-safe（7日）は設定で消せない**ので teardown 後も必ず残る。
  - ⚠️ データは sklearn 版 California Housing のみ。Snowflake-Labs の quickstart は
    Kaggle 版で列も目的変数スケールも別物（Snowflake 事前調査で判明した罠）。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.telemetry.schemas import MlRun, Platform, Stage, Tier, UnificationUnit
from core.telemetry.tracking import RunContext, RunSink
from platforms.shared.contracts.packaging import require_code_revision_stamp
from platforms.shared.contracts.tracking import TrackedOperations, artifact_uri_from

_logger = logging.getLogger("platforms.snowflake")

# sproc のハンドラ（warehouse 内で import される）
SPROC_HANDLER = "platforms.snowflake.sproc_handler.main"

# Anaconda channel にある名前で書く（pip の名前と一致しないものがある）。
#
# **バージョンを pyproject と同じ minor に固定する。** 無指定だと channel の最新が
# 入り、同一SHAでも RMSE がずれる（docs/01_requirements.md 破綻条件「5基盤で RMSE が
# 不一致 = 実装漏れ」）。原因が「Snowflake だけ数値が違う」形で現れると調査が長引くので、
# **ずれるより CREATE PROCEDURE の時点で落ちる方がよい**。
# 正本は pyproject.toml の dependencies（tests/test_snowflake_adapter.py が一致を pin）。
# ⚠️ Snowflake の PACKAGES 句が範囲指定子（>=,<）を受けるかは未確認
# （公式ドキュメントの例は `pkg==x.y.z` の完全一致のみ）。受けない場合は
# Phase 5 の precheck で channel の実提供版を確認し `lightgbm==4.6.x` 形へ
# 書き換える（Snowflake 事前調査の項目5）。
#
# **pyarrow は必須**。`session.table().to_pandas()` は connector の pandas 経路を通り、
# その実装が pyarrow を要求する（2026-08-01 実測: 入れないと warehouse 内で
# `255002: Optional dependency: 'pandas' is not installed` になる。
# メッセージは pandas と言うが、実際に足りないのは pyarrow）。
# 「Parquet を読むのは Tier A だけだから不要」という判断が誤りだった。
SPROC_PACKAGES: tuple[str, ...] = (
    "snowflake-snowpark-python",
    "lightgbm>=4.6,<4.7",
    "scikit-learn>=1.8,<1.9",
    "pandas>=2.3,<2.4",
    "pyarrow>=21.0,<22.0",
)

# Model Registry へ登録するモデルの conda 依存。
#
# **これを渡さないと登録が通らない**（sproc の PACKAGES とは別物）。既定では
# `ENABLE_PIP_ONLY_PACKAGING` により manifest が PyPI 参照になり、
# `CREATE MODEL` 時にサーバーが外部アクセスを要求する。
# 版は Anaconda channel の在庫に合わせる（`information_schema.packages` で確認できる）。
MODEL_CONDA_DEPENDENCIES: tuple[str, ...] = (
    "lightgbm==4.6.0",
    "scikit-learn==1.8.0",
)

# 接続情報の環境変数。**SDK 標準名なので改名しない**
# （docs/runbooks/credentials.md の命名規則の例外規定）。
ACCOUNT_ENV = "SNOWFLAKE_ACCOUNT"
USER_ENV = "SNOWFLAKE_USER"
PRIVATE_KEY_ENV = "SNOWFLAKE_PRIVATE_KEY"
PRIVATE_KEY_PATH_ENV = "SNOWFLAKE_PRIVATE_KEY_PATH"
PRIVATE_KEY_PASSPHRASE_ENV = "SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"
PASSWORD_ENV = "SNOWFLAKE_PASSWORD"

# キーペア認証は authenticator を明示しないと選ばれない
# （connector 4.7 の connection.py は `authenticator == KEY_PAIR_AUTHENTICATOR` でしか
# AuthByKeyPair を組み立てない。private_key を渡しただけでは切り替わらない）。
KEY_PAIR_AUTHENTICATOR = "SNOWFLAKE_JWT"


class SnowflakeConnectionError(RuntimeError):
    """接続情報が足りない。どの環境変数を埋めるかまで書いて投げる。

    `platforms.shared.config.ConfigError` を使わないのは循環 import になるため
    （config.py がこのモジュールを import している）。
    """


@dataclass(frozen=True)
class SnowflakeConfig:
    """Terraform の outputs（infra/environments/sf-dev）から埋める。

    account / user / 秘密鍵は環境変数（`SNOWFLAKE_*`）から `connection_parameters()` が
    読む。**秘密をこのオブジェクトに載せない。**
    """

    database: str
    schema: str
    warehouse: str
    # Terraform outputs の role_name（既定 `MCML_DEV_ROLE`）。空なら接続既定のロール。
    # **ここを ACCOUNTADMIN のままにしない。** Terraform は ACCOUNTADMIN で回すが、
    # adapter まで同じ権限で動かすと権限エラーが一度も起きず、本ラボ本命の
    # permission friction が Snowflake だけ常にゼロになる。
    role: str = ""
    stage: str = "CODE"
    model_name: str = "CALIFORNIA_HOUSING"
    procedure_name: str = "TRAIN"
    source_table: str = "CALIFORNIA_HOUSING"
    package_filename: str = "core_ml.zip"
    python_runtime_version: str = "3.12"
    predict_function: str = "predict"
    packages: tuple[str, ...] = SPROC_PACKAGES
    # Model Registry 登録時の conda 依存。**空にしない**（空だと pip 経路になり
    # 外部アクセスが要る = トライアルでは登録できない。register_model のコメント参照）。
    # sproc と違い registry 側は学習ライブラリだけでよい（実行は登録されたモデルが行う）。
    model_conda_dependencies: tuple[str, ...] = MODEL_CONDA_DEPENDENCIES
    tags: dict[str, str] = field(default_factory=lambda: {"project": "multicloud-ml-platform-lab"})

    @property
    def qualified_stage(self) -> str:
        return f"@{self.database}.{self.schema}.{self.stage}"

    @property
    def qualified_procedure(self) -> str:
        return f"{self.database}.{self.schema}.{self.procedure_name}"

    @property
    def qualified_model(self) -> str:
        """スキーマ内オブジェクト。モデルがデータと同じ名前空間に居る（Tier B の特徴）。"""
        return f"{self.database}.{self.schema}.{self.model_name}"

    @property
    def package_stage_path(self) -> str:
        return f"{self.qualified_stage}/dist/{self.package_filename}"


def connection_parameters(
    config: SnowflakeConfig, environ: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Snowpark Session に渡す接続パラメータを組む。

    **connector には `SNOWFLAKE_ACCOUNT` / `SNOWFLAKE_USER` の env フォールバックが無い。**
    さらに `Session.builder.configs()` に1つでも値を渡すと connections.toml も
    読まれなくなる（snowpark 1.42 の `SessionBuilder._create_internal` は
    `not self._options` のときだけ既定接続を読む）。実測: database/schema/warehouse だけ
    渡すと env を設定していても `251005: User is empty` で落ちる。
    だからここで env から明示的に組む。

    ロールは env ではなく **config（= terraform outputs の `role_name`）から取る**。
    `SNOWFLAKE_ROLE` は Terraform provider 用（ACCOUNTADMIN）であって、
    adapter が名乗るロールではない（`SnowflakeConfig.role` のコメント参照）。
    """
    values = os.environ if environ is None else environ

    account = (values.get(ACCOUNT_ENV) or "").strip()
    user = (values.get(USER_ENV) or "").strip()
    missing = [name for name, value in ((ACCOUNT_ENV, account), (USER_ENV, user)) if not value]
    if missing:
        raise SnowflakeConnectionError(
            f"{' / '.join(missing)} が未設定。Doppler 経由で渡す"
            "（docs/runbooks/credentials.md §5。account は <org>-<account> 形式）"
        )

    parameters: dict[str, Any] = {
        "account": account,
        "user": user,
        "database": config.database,
        "schema": config.schema,
        "warehouse": config.warehouse,
    }
    if config.role:
        parameters["role"] = config.role
    parameters.update(_authentication(values))
    return parameters


def _authentication(values: Mapping[str, str]) -> dict[str, Any]:
    """認証方式を解決する。キーペア（PEM → ファイル）> パスワードの順。

    PEM 文字列をそのまま `private_key` に渡してはいけない。connector は str を
    **base64 DER** として復号する（auth/keypair.py）ので、PEM は DER へ変換して
    bytes で渡す。一時ファイルに書き出さないのは、秘密鍵をディスクに落とさないため。
    """
    passphrase = values.get(PRIVATE_KEY_PASSPHRASE_ENV) or None

    pem = values.get(PRIVATE_KEY_ENV)
    if pem:
        return {
            "authenticator": KEY_PAIR_AUTHENTICATOR,
            "private_key": _der_from_pem(pem, passphrase),
        }

    key_path = values.get(PRIVATE_KEY_PATH_ENV)
    if key_path:
        parameters: dict[str, Any] = {
            "authenticator": KEY_PAIR_AUTHENTICATOR,
            "private_key_file": key_path,
        }
        if passphrase:
            parameters["private_key_file_pwd"] = passphrase
        return parameters

    password = values.get(PASSWORD_ENV)
    if password:
        # 単要素パスワードでのプログラム接続は塞がれることがある。
        # 落ちたら回避策を足さずキーペアへ寄せる（試行は ml_runs に残る）。
        return {"password": password}

    raise SnowflakeConnectionError(
        f"{PRIVATE_KEY_ENV} / {PRIVATE_KEY_PATH_ENV} / {PASSWORD_ENV} のいずれも未設定。"
        "キーペア認証を既定にする（docs/runbooks/credentials.md §5）"
    )


def _der_from_pem(pem: str, passphrase: str | None) -> bytes:
    """PKCS#8 PEM を connector が受ける DER bytes に変換する。"""
    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415 - 接続時のみ

    key = serialization.load_pem_private_key(
        pem.encode("utf-8"),
        password=passphrase.encode("utf-8") if passphrase else None,
    )
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


class SnowflakeAdapter(TrackedOperations):
    """Golden Path ステップ2〜3 の Snowflake 実装。

    使い方:

        adapter = SnowflakeAdapter(config, sink=sink)
        adapter.upload_to_stage(Path("artifacts/dist/core_ml.zip"))
        train = adapter.submit_training({"num_leaves": 31})
        adapter.register_model(adapter.model_artifact_uri(train))
        adapter.deploy(adapter.model_version)
        adapter.predict_one({...})
        adapter.teardown()
    """

    platform = Platform.SNOWFLAKE
    tier = Tier.B
    unification_unit = UnificationUnit.PACKAGE

    def __init__(
        self,
        config: SnowflakeConfig,
        *,
        sink: RunSink,
        session: Any | None = None,
        registry_factory: Callable[[Any], Any] | None = None,
        model_loader: Callable[[Path], Any] | None = None,
    ) -> None:
        self._config = config
        self._sink = sink
        self._session = session
        self._registry_factory = registry_factory
        self._model_loader = model_loader
        self._registry: Any | None = None
        self.model_version: str | None = None

    # --- SDK 解決（遅延 import）-------------------------------------------

    @property
    def session(self) -> Any:
        """Snowpark Session。接続情報は環境変数、ロールは config から解決する。

        組み立ての理由は `connection_parameters()` の docstring。
        """
        if self._session is None:
            from snowflake.snowpark import Session  # noqa: PLC0415 - 遅延 import は意図的

            self._session = Session.builder.configs(connection_parameters(self._config)).create()
        return self._session

    @property
    def registry(self) -> Any:
        """Snowflake Model Registry（snowflake-ml-python）。"""
        if self._registry is None:
            if self._registry_factory is not None:
                self._registry = self._registry_factory(self.session)
            else:
                from snowflake.ml.registry import Registry  # noqa: PLC0415

                self._registry = Registry(
                    session=self.session,
                    database_name=self._config.database,
                    schema_name=self._config.schema,
                )
        return self._registry

    # --- stage（Tier B の統一単位）---------------------------------------

    def build_package(self, source_dir: Path, output_dir: Path) -> Path:
        """sproc が IMPORTS で読む zip を作る。**Tier B の同一性担保はこの成果物。**

        入れるのは `core`（学習本体）と `platforms/{__init__,snowflake,neon}`
        （sproc ハンドラと JSONL 退避）。クラウド SDK を使う他基盤の adapter は
        入れない —— warehouse の Anaconda channel には無く、import 時点で壊れる。

        zip は Databricks の wheel と違って**メタデータを持たない**ので、
        `_stamp.py` が唯一の code_revision の運び手になる（先に検証する）。
        """
        require_code_revision_stamp(source_dir)
        src = Path(source_dir) / "src"
        included = (
            src / "core",
            src / "platforms" / "__init__.py",
            src / "platforms" / "neon",
            src / "platforms" / "snowflake",
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        package = output_dir / self._config.package_filename

        with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
            for target in included:
                paths = sorted(target.rglob("*.py")) if target.is_dir() else [target]
                for path in paths:
                    if "__pycache__" in path.parts:
                        continue
                    archive.write(path, path.relative_to(src))
        return package

    def upload_to_stage(self, package: Path) -> str:
        """`src/core/ml` を含む zip を stage へ PUT する。

        auto_compress は切る（sproc の IMPORTS が読むのは .zip そのもの。
        再圧縮されると import できない）。
        """
        target = f"{self._config.qualified_stage}/dist"
        self.session.file.put(
            f"file://{package}",
            target,
            auto_compress=False,
            overwrite=True,
        )
        return self._config.package_stage_path

    # --- PlatformAdapter -------------------------------------------------

    def submit_training(self, params: dict[str, Any]) -> MlRun:
        """stored procedure を宣言して CALL する（warehouse 実行）。

        Tier A のようなジョブ資源は無く、**DDL + CALL** が実行のすべて。
        CREATE OR REPLACE なので、パッケージを入れ替えたら宣言し直せばよい。

        run_id / attempt を payload で渡し、**成功時の ml_runs 行は sproc 側が書く**
        （job_record.py の所有者規約）。ただし warehouse に psycopg は無いので
        sproc は Neon へ直接届かず、必ず JSONL を stage へ出す = collected。
        **その到達不能自体が Tier B の比較データ**（docs/01_requirements.md 破綻条件）。
        """
        cfg = self._config
        attempt = self._next_attempt(Stage.TRAIN)

        def call(ctx: RunContext) -> None:
            self._create_procedure()
            payload = dict(params)
            payload["source_table"] = cfg.source_table
            payload["stage_path"] = f"{cfg.qualified_stage}/runs/{ctx.run_id}"
            payload["run_id"] = ctx.run_id
            payload["attempt"] = attempt

            rows = self.session.sql(
                f"CALL {cfg.qualified_procedure}(PARSE_JSON('{json.dumps(payload)}'))"
            ).collect()
            result = self._first_value(rows)
            summary = json.loads(result) if isinstance(result, str) else (result or {})

            metrics = summary.get("metrics") or {}
            ctx.metrics.update(metrics)
            ctx.params["procedure"] = cfg.qualified_procedure
            ctx.params["model_artifact_uri"] = payload["stage_path"]
            ctx.params["remote_run_id"] = summary.get("run_id")

        return self._tracked(
            Stage.TRAIN, dict(params), call, attempt=attempt, job_owns_success=True
        )

    def register_model(self, artifact_uri: str) -> MlRun:
        """stage の成果物を Model Registry へ登録する。

        Registry はモデルオブジェクトを要求するので、stage から model.txt を取り出して
        LightGBM Booster に戻してから log_model する。**成果物そのものではなく
        「復元したモデル」を登録する**点が Tier A（artifact URI を渡すだけ）との差。

        さらに **`sample_input_data` が必須**（2026-08-01 実測。無いと
        `ValueError (2110): Either of signatures or sample_input_data must be provided`）。
        Registry は入力スキーマを保存して SQL 関数の引数を組み立てるため、
        「モデルの実体」だけでなく「入力の形」まで要求する。
        Tier A は artifact URI を渡すだけなので、**登録に要する情報量が最も多い基盤**。
        """
        cfg = self._config

        def call(ctx: RunContext) -> None:
            with tempfile.TemporaryDirectory() as tmp:
                local = Path(tmp)
                self.session.file.get(f"{artifact_uri}/model.txt", str(local))
                model_file = local / "model.txt"
                if not model_file.exists():
                    raise RuntimeError(f"stage から model.txt を取得できない: {artifact_uri}")
                model = self._load_model(model_file)

            version = self.registry.log_model(
                model,
                model_name=cfg.model_name,
                comment=f"run_id={ctx.run_id}",
                metrics=dict(ctx.metrics),
                sample_input_data=self._sample_input(),
                # **conda を明示しないと外部アクセスが要る pip 経路になる。**
                # 既定（capability `ENABLE_PIP_ONLY_PACKAGING=true`）では manifest に
                #   artifact_repository_map: {pip: SNOWFLAKE.SNOWPARK.PYPI_SHARED_REPOSITORY}
                # が書かれ、CREATE MODEL 時にサーバーが PyPI を取りに行く。
                # トライアルは external access 不可なので `603 internal error` になり、
                # **クライアント側は全て成功しているため原因が一切出ない**（2026-08-01 実測）。
                # conda_dependencies を渡すと Anaconda channel 経路になり外部アクセスが要らない。
                conda_dependencies=list(cfg.model_conda_dependencies),
            )
            version_name = getattr(version, "version_name", None) or str(version)
            self.model_version = version_name
            ctx.params["model_full_name"] = cfg.qualified_model
            ctx.params["model_version"] = version_name

        return self._tracked(Stage.REGISTER, {"artifact_uri": artifact_uri}, call)

    def deploy(self, model_ref: str) -> MlRun:
        """既定バージョンを切り替える。**インフラは何も作らない**。

        Snowflake は登録した時点で warehouse から SQL で呼べるため、
        Tier A の「エンドポイントを立てる」に相当する操作が存在しない。
        `no_endpoint_resource=True` を記録して、比較表でこの非対称性を残す。
        （SPCS でサービス化する道はあるが主経路にしない = 要件で決定済み）
        """
        cfg = self._config

        def call(ctx: RunContext) -> None:
            model = self.registry.get_model(cfg.model_name)
            model.default = str(model_ref)
            ctx.params["model_full_name"] = cfg.qualified_model
            ctx.params["default_version"] = str(model_ref)
            ctx.params["no_endpoint_resource"] = True

        return self._tracked(Stage.DEPLOY, {"model_ref": model_ref}, call)

    def predict_one(self, instance: dict[str, Any]) -> MlRun:
        """warehouse 推論を1件（フェーズ完了条件⑤）。

        HTTP エンドポイントではなく **SQL/Python API 経由**。3契約の HTTP 形は通らない。
        """
        cfg = self._config

        def call(ctx: RunContext) -> None:
            import pandas as pd  # noqa: PLC0415 - core 依存だが adapter でのみ使う

            model = self.registry.get_model(cfg.model_name)
            version = model.version(self.model_version) if self.model_version else model.default
            frame = pd.DataFrame([instance])
            result = version.run(frame, function_name=cfg.predict_function)
            predictions = self._extract_predictions(result)
            if not predictions:
                raise RuntimeError("predictions が空。モデル版と入力列を確認")
            ctx.metrics["prediction"] = predictions[0]

        return self._tracked(Stage.PREDICT, {"instance": instance}, call)

    def teardown(self) -> MlRun:
        """stored procedure を落とす。

        **エンドポイントが無いので止めるべき常時課金も無い**（warehouse は auto-suspend）。
        残るのは Tier A と種別の違う残留:
          - Model Registry のモデル版（カタログ内オブジェクト）
          - stage 上の成果物・パッケージ
          - Time Travel（`data_retention_time_in_days`）と **Fail-safe（7日・設定で消せない）**
        """
        cfg = self._config

        def call(ctx: RunContext) -> None:
            self.session.sql(
                f"DROP PROCEDURE IF EXISTS {cfg.qualified_procedure}(VARIANT)"
            ).collect()
            ctx.params["removed"] = [f"procedure:{cfg.qualified_procedure}"]
            ctx.params["residual_model_version"] = (
                f"{cfg.qualified_model}:{self.model_version}" if self.model_version else None
            )
            ctx.params["residual_stage_path"] = cfg.package_stage_path
            # Fail-safe は設定で消せない。残留比較で Tier A と同じ土俵に置かない
            ctx.params["residual_fail_safe_days"] = 7

        return self._tracked(Stage.TEARDOWN, {"action": "teardown"}, call)

    # --- 補助 -------------------------------------------------------------

    def model_artifact_uri(self, train_run: MlRun) -> str:
        """train の MlRun から成果物 URI を取り出す（5基盤共通）。"""
        return artifact_uri_from(train_run)

    def _create_procedure(self) -> None:
        """sproc を宣言する。IMPORTS が stage 上のパッケージを指す = 統一単位の担保。"""
        cfg = self._config
        packages = ", ".join(f"'{p}'" for p in cfg.packages)
        self.session.sql(
            f"CREATE OR REPLACE PROCEDURE {cfg.qualified_procedure}(PARAMS VARIANT)\n"
            "  RETURNS VARIANT\n"
            "  LANGUAGE PYTHON\n"
            f"  RUNTIME_VERSION = '{cfg.python_runtime_version}'\n"
            f"  PACKAGES = ({packages})\n"
            f"  IMPORTS = ('{cfg.package_stage_path}')\n"
            f"  HANDLER = '{SPROC_HANDLER}'"
        ).collect()

    def _sample_input(self) -> Any:
        """`log_model` に渡す入力サンプル（signature の材料）。

        列名・順序は学習と同じ `FEATURE_COLUMNS` を正とする。ここがずれると
        登録された SQL 関数の引数がずれ、推論だけ静かに壊れる。
        """
        import pandas as pd  # noqa: PLC0415 - 登録時のみ必要

        from core.ml.config.constants import FEATURE_COLUMNS  # noqa: PLC0415

        return pd.DataFrame([{column: 0.0 for column in FEATURE_COLUMNS}])

    def _load_model(self, model_file: Path) -> Any:
        if self._model_loader is not None:
            return self._model_loader(model_file)
        import lightgbm as lgb  # noqa: PLC0415 - Registry 登録時のみ必要

        return lgb.Booster(model_file=str(model_file))

    @staticmethod
    def _first_value(rows: Any) -> Any:
        """CALL の戻り（1行1列）を取り出す。Row / tuple / dict を吸収する。"""
        if not rows:
            return None
        row = rows[0]
        if isinstance(row, dict):
            return next(iter(row.values()), None)
        if isinstance(row, list | tuple):
            return row[0] if row else None
        as_dict = getattr(row, "as_dict", None)
        if callable(as_dict):
            return next(iter(as_dict().values()), None)
        return row

    @staticmethod
    def _extract_predictions(result: Any) -> list[Any]:
        """run() の戻り（pandas DataFrame 想定）から予測列を取り出す。"""
        to_pandas = getattr(result, "to_pandas", None)
        frame = to_pandas() if callable(to_pandas) else result
        columns = getattr(frame, "columns", None)
        if columns is None:
            return list(frame or [])
        target = next(
            (c for c in columns if str(c).lower() in {"output_feature_0", "prediction", "predict"}),
            columns[-1],
        )
        return list(frame[target])
