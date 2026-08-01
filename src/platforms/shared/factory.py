"""5基盤の adapter を同じ形で組み立てる。

`config.Settings` が「設定を解決する」層、ここは「解決済み設定から adapter を作る」層。
分けているのは、Settings がクラウド SDK を知らないまま保てるため
（adapter の import は遅延なので実際には軽い）。

## deploy に渡す値の差（そのまま比較レポートの材料）

`register_model()` の後に `deploy()` へ何を渡すかが5基盤で違う。ここは
**共通化して隠さない**（差が成果物）。呼び出し側が毎回思い出さなくて済むよう、
差だけをこのモジュールの1関数に集約する。

    Vertex     モデルのリソース名（projects/.../models/...）
    SageMaker  **成果物の S3 URI**（Model を deploy 時に作るため）
    Azure ML   name:version
    Databricks モデル**バージョン番号**（UC の登録名は config が持つ）
    Snowflake  モデルバージョン名（既定バージョンの切り替えだけ）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.telemetry.schemas import Platform
from core.telemetry.tracking import RunSink
from platforms.azureml.adapter import AzureMLAdapter
from platforms.databricks.adapter import DatabricksAdapter
from platforms.sagemaker.adapter import SageMakerAdapter
from platforms.shared.config import Settings, load_settings
from platforms.shared.contracts.tracking import NEON_POOLED_URI_ENV, artifact_uri_from
from platforms.snowflake.adapter import SnowflakeAdapter
from platforms.vertex.adapter import VertexAdapter

ADAPTERS: dict[Platform, type] = {
    Platform.VERTEX: VertexAdapter,
    Platform.SAGEMAKER: SageMakerAdapter,
    Platform.AZUREML: AzureMLAdapter,
    Platform.DATABRICKS: DatabricksAdapter,
    Platform.SNOWFLAKE: SnowflakeAdapter,
}

DEFAULT_FALLBACK_DIR = Path("artifacts/fallback")


def build_adapter(platform: Platform, *, sink: RunSink, settings: Settings | None = None) -> Any:
    """解決済み設定から adapter を作る。設定不足は ConfigError で落ちる。

    基盤固有の付加機能（例: Vertex の Experiments 複写）はここで配線しない。
    それは **その基盤の config フィールド**（`VertexConfig.experiment`）であり、
    adapter が自分で組み立てる。共通 factory が単一基盤の機能を知り始めると、
    「5基盤に共通する形だけを共通層に置く」原則（ports.py）が崩れる
    —— 2026-08-02 に build_observer をここへ置いてその原則を破り、作り直した。
    """
    resolved = settings if settings is not None else load_settings()
    return ADAPTERS[platform](resolved.for_platform(platform), sink=sink)


def build_sink(platform: Platform, *, fallback_dir: Path | None = None) -> RunSink:
    """記録先を決める。Neon が使えなければ JSONL へ（`make collect` で後から合流）。

    **オーケストレータ側の記録先**であって、ジョブ内の記録先ではない
    （そちらは platforms/neon/job_record.py が自分で決める）。

    環境変数名は contracts.tracking から取る。connection.py から import すると
    その時点で psycopg が要求され、**psycopg の無い環境で JSONL 経路まで死ぬ**。

    """
    import os  # noqa: PLC0415 - 環境判定のみ

    directory = (fallback_dir or DEFAULT_FALLBACK_DIR) / platform.value
    if not os.environ.get(NEON_POOLED_URI_ENV):
        from core.telemetry.sinks import JsonlRunSink  # noqa: PLC0415

        return JsonlRunSink(directory)

    from platforms.neon.run_sink import NeonRunSink  # noqa: PLC0415 - psycopg 依存

    return NeonRunSink()



def deploy_reference(
    adapter: Any,
    train_run: Any,
    *,
    artifact_uri: str | None = None,
    model_ref: str | None = None,
) -> str:
    """`deploy()` へ渡す値を基盤ごとに解決する（差はモジュール docstring 参照）。

    `artifact_uri` は train を飛ばして再開する場合の明示指定（SageMaker のみ使う）。
    `model_ref` は **登録し直さずに ⑤ をやり直す**ための明示指定（`--artifact-uri` と同じ理由。
    register をやり直すとカタログに版が増え、残留と friction の両方が濁る）。
    """
    platform = adapter.platform
    if model_ref and platform is not Platform.SAGEMAKER:
        return model_ref
    if platform is Platform.SAGEMAKER:
        # Endpoint は Model を要求し、Model は成果物を要求する（器を先に作れない）
        if train_run is not None:
            return artifact_uri_from(train_run)
        if artifact_uri:
            return artifact_uri
        raise ValueError("sagemaker: deploy は学習成果物の URI を要求する（train が必要）")
    if platform in (Platform.VERTEX, Platform.AZUREML):
        reference = adapter.model_resource_name
    else:  # Databricks / Snowflake はカタログ内オブジェクトの「版」
        reference = adapter.model_version
    if not reference:
        raise ValueError(
            f"{platform.value}: deploy に渡す参照が未解決（register_model が成功しているか確認）"
        )
    return str(reference)
