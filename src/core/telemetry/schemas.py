"""計測スキーマ。

DDL 正本は sql/schema.sql、設計正本は docs/05_data_model.md。
本モジュールはその Python 側の写しであり、値の取りうる範囲をここで固定する。

入力データ契約（特徴量の列名・seed）は関心が違うので core/ml/constants.py。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Platform(str, Enum):
    VERTEX = "vertex"
    SAGEMAKER = "sagemaker"
    AZUREML = "azureml"
    DATABRICKS = "databricks"
    SNOWFLAKE = "snowflake"


class Tier(str, Enum):
    """A = コンテナ実行型 / B = データ基盤内蔵型（docs/02_architecture.md）。"""

    A = "A"
    B = "B"


class UnificationUnit(str, Enum):
    """Tier A は同一コンテナ、Tier B は同一 wheel/stage パッケージで同一性を担保する。"""

    CONTAINER = "container"
    PACKAGE = "package"


class Stage(str, Enum):
    """完了条件②〜⑤ + 撤退。

    `TEARDOWN` は 2026-08-01 に追加した。それ以前は teardown を
    `DEPLOY` + `params.action='teardown'` として記録しており、
    permission friction / stage 別所要のクエリが deploy 行に teardown の
    attempt と所要（332〜547s）を混ぜていた（比較の正本が汚れていた）。

    **過去行は書き換えない**（「計測データを消さない」規約）。
    旧形式は `sql/comparison_queries.sql` 側で除外して読む。
    """

    TRAIN = "train"
    REGISTER = "register"
    DEPLOY = "deploy"
    PREDICT = "predict"
    TEARDOWN = "teardown"


class Status(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"


class FailureClass(str, Enum):
    """失敗の分類。permission friction クエリの核心。

    成功だけを記録すると「最小権限で通るまで何回直したか」が消えるため、
    失敗した試行も attempt を増やして必ず記録する。
    """

    IAM = "iam"
    QUOTA = "quota"
    CONTAINER = "container"
    PACKAGE = "package"
    NETWORK = "network"
    SDK = "sdk"
    DATA = "data"
    NONE = "none"


class WritePath(str, Enum):
    """Neon への到達経路。到達可否そのものが比較軸なので必須項目。"""

    DIRECT = "direct"
    COLLECTED = "collected"


class InfraAction(str, Enum):
    APPLY = "apply"
    DESTROY = "destroy"


# --- 計測レコード ---------------------------------------------------------


@dataclass
class MlRun:
    """ml_runs 1行。学習ジョブ本体とトランザクションを共有しない。"""

    run_id: str
    platform: Platform
    tier: Tier
    unification_unit: UnificationUnit
    stage: Stage
    status: Status
    code_revision: str
    write_path: WritePath
    attempt: int = 1
    duration_seconds: float | None = None
    failure_class: FailureClass | None = None
    error_excerpt: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None


@dataclass
class InfraEvent:
    """infra_events 1行。residual_resources が残留リソース比較の一次データ。"""

    event_id: str
    platform: Platform
    action: InfraAction
    status: Status
    duration_seconds: float | None = None
    resource_count: int | None = None
    residual_resources: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None


@dataclass
class CostSnapshot:
    """cost_snapshots 1行。請求反映に1〜2日遅れるため実行時には確定しない。"""

    platform: Platform
    usage_date: str
    service: str
    amount_usd: float
