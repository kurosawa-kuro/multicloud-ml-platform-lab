"""マネージドパイプラインの定義・投入（修正11）。

    doppler run -- python scripts/run_pipeline.py sagemaker build     # 定義だけ（課金ゼロ）
    doppler run -- python scripts/run_pipeline.py sagemaker submit    # 投入（課金あり）
    doppler run -- python scripts/run_pipeline.py azureml  build
    doppler run -- python scripts/run_pipeline.py azureml  submit

## 対応基盤（**Vertex は対象外**）

Vertex AI Pipelines は step が**コンテナ実行**で、`run_phase.py` を動かす器
（orchestrator イメージ）が要る。学習イメージは依存最小でそれを持てず、実投入で
落ちることを確認済み → **P3 見送り**（修正11 のノート）。ここに vertex を足したく
なったら、器の追加とセットで owner 判断が要る。

SageMaker / Azure ML は step が**宣言**（学習ジョブの型付き宣言 / command job の合成）
なので器が要らない。載せるのは adapter が CLI 投入で使うのと**同じ仕様**。

## build と submit を分ける理由

`build` はクラウドを一切叩かない（課金ゼロ）。「定義が組めること」と
「投入して動くこと」は別の証拠なので、前者だけを何度でも確認できるようにする。
実際 Vertex では compile が通ったのに投入で落ちた —— **compile 成功を「動く」の
証拠にしない**（修正11 の教訓）。

exit code 規約（.claude/rules/scripts.md）: 0=成功 / 1=投入失敗 / 2=引数・設定不備
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from typing import Any

from core.telemetry.schemas import Platform
from platforms.shared.config import ConfigError, load_settings
from platforms.shared.contracts.tracking import telemetry_env

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2

# 器（orchestrator イメージ）が要らない = パイプライン化できる基盤だけを載せる。
SUPPORTED = ("sagemaker", "azureml")

# 定義を作る時点で run_id / attempt が確定する（パイプライン定義は静的）。
# 「1定義 = 1回の比較試行」として扱う。
DEFAULT_ATTEMPT = 1


def build_sagemaker(params: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    """SageMaker Pipelines の定義と、投入に使う adapter を返す。"""
    from platforms.sagemaker.adapter import SageMakerAdapter
    from platforms.sagemaker.pipeline import build_definition

    config = load_settings().for_platform(Platform.SAGEMAKER)
    adapter = SageMakerAdapter(config, sink=_null_sink())
    request = adapter.training_request(str(uuid.uuid4()), DEFAULT_ATTEMPT, params, telemetry_env())
    return build_definition(request), adapter


def build_azureml(params: dict[str, Any]) -> tuple[Any, Any]:
    """Azure ML Pipelines の PipelineJob と、投入に使う adapter を返す。"""
    from platforms.azureml.adapter import AzureMLAdapter
    from platforms.azureml.pipeline import build_pipeline

    config = load_settings().for_platform(Platform.AZUREML)
    adapter = AzureMLAdapter(config, sink=_null_sink())
    job = build_pipeline(
        adapter, str(uuid.uuid4()), DEFAULT_ATTEMPT, json.dumps(params), telemetry_env()
    )
    return job, adapter


def _null_sink() -> Any:
    """定義を組むだけなら記録先は要らない。

    **Neon へ繋がない**のは、build がクラウドも DB も叩かないことを保証するため
    （`NeonRunSink()` を作ると接続が張られる）。投入後の run 行はジョブ側が書く。
    """

    class _Null:
        def record_run(self, run: Any) -> Any:
            from core.telemetry.schemas import WritePath

            return WritePath.DIRECT

        def next_attempt(self, platform: Any, stage: Any) -> int:
            return DEFAULT_ATTEMPT

    return _Null()


def submit_sagemaker(definition: dict[str, Any], adapter: Any, name: str) -> str:
    """`CreatePipeline`（あれば更新）→ `StartPipelineExecution`。"""
    body = json.dumps(definition)
    token = str(uuid.uuid4())
    role = adapter._config.execution_role_arn
    try:
        adapter.sagemaker.create_pipeline(
            PipelineName=name,
            PipelineDefinition=body,
            ClientRequestToken=token,
            RoleArn=role,
        )
    except Exception as exc:  # noqa: BLE001 - 既存なら更新へ落とす
        if "already exists" not in str(exc).lower():
            raise
        adapter.sagemaker.update_pipeline(
            PipelineName=name, PipelineDefinition=body, RoleArn=role
        )
    started = adapter.sagemaker.start_pipeline_execution(
        PipelineName=name, ClientRequestToken=str(uuid.uuid4())
    )
    return str(started.get("PipelineExecutionArn", name))


def submit_azureml(job: Any, adapter: Any) -> str:
    """`MLClient.jobs.create_or_update` でパイプラインジョブを投入する。"""
    created = adapter.client.jobs.create_or_update(job)
    return str(getattr(created, "name", created))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_pipeline", description=__doc__)
    parser.add_argument("platform", choices=SUPPORTED)
    parser.add_argument("action", choices=("build", "submit"))
    parser.add_argument("--params", default="{}", help="学習ハイパーパラメータの JSON")
    parser.add_argument("--name", default="mcml-train", help="パイプライン名（SageMaker）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        params = json.loads(args.params)
        if not isinstance(params, dict):
            raise ValueError("--params は JSON オブジェクトであること")
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"入力エラー: {exc}", file=sys.stderr)
        return EXIT_USAGE

    try:
        if args.platform == "sagemaker":
            definition, adapter = build_sagemaker(params)
            steps = [s["Name"] for s in definition["Steps"]]
            if not steps:
                print("パイプライン定義にステップが無い", file=sys.stderr)
                return EXIT_FAILED
            print(f"-- 定義を組んだ: steps={steps}")
            if args.action == "build":
                return EXIT_OK
            reference = submit_sagemaker(definition, adapter, args.name)
        else:
            job, adapter = build_azureml(params)
            steps = list(getattr(job, "jobs", {}) or {})
            # **空定義を投入しない。** node 化を忘れると jobs が空のまま作られ、
            # 投入は成功するのに何も動かない（2026-08-02 実測）
            if not steps:
                print("パイプライン定義にステップが無い（node 化の漏れ）", file=sys.stderr)
                return EXIT_FAILED
            print(f"-- 定義を組んだ: steps={steps}")
            if args.action == "build":
                return EXIT_OK
            reference = submit_azureml(job, adapter)
    except ConfigError as exc:
        print(f"設定エラー: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except Exception as exc:  # noqa: BLE001 - 投入失敗は exit 1（記録はジョブ側）
        print(f"投入に失敗: {exc}", file=sys.stderr)
        return EXIT_FAILED

    print(f"-- 投入した: {reference}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
