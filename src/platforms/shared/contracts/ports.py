"""基盤 adapter の共通契約。

5基盤の adapter が同じ形を持つことで、比較レポートの各行が
「同じ操作を各基盤でやったときの差」になる。形が揃っていないと
所要時間も試行回数も比較にならない。

実装は src/platforms/{vertex,sagemaker,azureml,databricks,snowflake}/。
このモジュールはクラウド SDK を import しない（core/ml の依存最小制約に合わせる）。

## port を切る基準

**5基盤ぶんの実装が並び、かつ呼び出し側が差を意識してはいけない操作だけ** port にする。
それ以外は切らない。抽象化が目的化すると、比較したい差までインタフェースの下に
隠れて見えなくなる（このプロジェクトでは差を見ることが成果物）。

port にする:
    PlatformAdapter  学習投入 / 登録 / デプロイ / 1件推論 / teardown
                     → 5実装。各操作の所要時間と試行回数がそのまま比較データになる
    ArtifactStore    成果物の upload / download
                     → 5実装（GCS / S3 / Blob / UC Volume / stage）

port にしない:
    データ入力       → 各基盤が pandas DataFrame を作るところまで責任を持ち、
                       core/ml はそれを受け取るだけ。**DataFrame 自体が境界**なので
                       上にさらに port を重ねない。各基盤の実装は1〜2行
                       （read_parquet / spark…toPandas() / session.table().to_pandas()）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from core.telemetry.schemas import MlRun, Platform, Tier, UnificationUnit


@runtime_checkable
class PlatformAdapter(Protocol):
    """学習投入 → 登録 → デプロイ → 1件推論の4操作。

    Golden Path のステップ2〜3（docs/02_architecture.md）に対応する。
    各メソッドは MlRun を返し、失敗時も failure_class を埋めて返す
    （例外を投げっぱなしにすると permission friction が測れない）。
    """

    platform: Platform
    tier: Tier
    unification_unit: UnificationUnit

    def submit_training(self, params: dict[str, Any]) -> MlRun:
        """学習ジョブを投入し完了を待つ。stage=train。"""
        ...

    def register_model(self, artifact_uri: str) -> MlRun:
        """モデルレジストリへ登録する。stage=register。"""
        ...

    def deploy(self, model_ref: str) -> MlRun:
        """オンライン推論エンドポイントへデプロイする。stage=deploy。

        Tier A は常時課金のためフェーズ末に必ず teardown すること。
        """
        ...

    def predict_one(self, instance: dict[str, Any]) -> MlRun:
        """1件だけオンライン推論する。stage=predict。完了条件⑤。"""
        ...

    def teardown(self) -> MlRun:
        """デプロイしたものを落とす。残留検査はこの後に行う。"""
        ...


@runtime_checkable
class ArtifactStore(Protocol):
    """各基盤のオブジェクトストレージ差を吸収する。

    GCS / S3 / Blob / UC Volume / Snowflake stage を横並びにする。
    """

    def upload_dir(self, local_dir: Path, remote_uri: str) -> str: ...

    def download_dir(self, remote_uri: str, local_dir: Path) -> Path: ...
