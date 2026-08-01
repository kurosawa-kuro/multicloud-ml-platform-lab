"""学習エントリポイント（5基盤共通）。

流用元: private-ops starter-kit `housing_ml/cli.py`。
**変更点**: GCS / BigQuery のオプションを削除し、`--input` を追加した。
処理本体は `core.ml.pipelines.train_pipeline` にあり、ここは薄い CLI。

**モデル学習そのものは `core.ml.training`**（LightGBM の呼び出し）。
このパッケージは実行インタフェースであって学習ロジックを持たない。

    Tier A (Vertex / SageMaker / Azure ML)
        entrypoint シム（docker/training/entrypoint_*.sh）が最終的にこの形へ落とす:
        python -m core.ml.cli --input "$INPUT_DIR" --output "$MODEL_DIR" --params "$PARAMS_JSON"

    Tier B (Databricks wheel task / Snowflake stored procedure)
        CLI を経由せず run_training_pipeline() を直接呼ぶ:
        run_training_pipeline(session.table("CALIFORNIA_HOUSING").to_pandas(), config)

出力 artifact: model.txt / metrics.json / feature_importance.csv / run.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core.ml.config.constants import RANDOM_SEED
from core.ml.data.load import read_parquet
from core.ml.data.validate import DatasetContractError
from core.ml.pipelines.train_pipeline import TrainingConfig, run_training_pipeline


def build_parser() -> argparse.ArgumentParser:
    """CLI 契約。ここを変えると3基盤の entrypoint シムが同時に壊れる。"""
    parser = argparse.ArgumentParser(
        prog="core.ml.cli",
        description="5基盤共通の学習エントリポイント",
    )
    parser.add_argument("--input", dest="input_dir", type=Path, required=True)
    parser.add_argument("--output", dest="output_dir", type=Path, required=True)
    parser.add_argument(
        "--params",
        default="{}",
        help="ハイパーパラメータの JSON 文字列。SageMaker は "
        "/opt/ml/input/config/hyperparameters.json をシムが読んで渡す",
    )
    parser.add_argument("--run-id", help="既定は uuid4（registry.manifest.new_run_id）")
    parser.add_argument(
        "--with-baseline",
        action="store_true",
        help="RandomForest ベースラインも学習する（Phase 0 の健全性チェック用。"
        "5基盤のジョブでは付けない = 比較したい実行時間に無関係な負荷を乗せない）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """exit code 規約: 0=成功 / 1=学習失敗 / 2=引数・設定不備。"""
    args = build_parser().parse_args(argv)

    try:
        params = json.loads(args.params)
        if not isinstance(params, dict):
            raise ValueError("--params は JSON オブジェクトであること")
        df = read_parquet(args.input_dir)
    except (json.JSONDecodeError, ValueError, FileNotFoundError) as exc:
        print(f"入力エラー: {exc}", file=sys.stderr)
        return 2

    try:
        result = run_training_pipeline(
            df,
            TrainingConfig(
                output_dir=args.output_dir,
                seed=int(params.get("seed", RANDOM_SEED)),
                run_id=args.run_id,
                params=params,
                with_baseline=args.with_baseline,
            ),
        )
    except DatasetContractError as exc:
        print(f"入力契約違反: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - 学習失敗は 1 で返す契約
        print(f"学習失敗: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "run_id": result.run_id,
                "metrics": result.metrics,
                "rows": int(len(df)),
                "output_dir": str(result.output_dir),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
