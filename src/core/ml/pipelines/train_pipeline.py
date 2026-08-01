"""学習パイプライン（5基盤共通の入口）。

流用元: private-ops starter-kit `housing_ml/pipelines/train_pipeline.py`。

**変更点**
1. 入力を DataFrame にした（sklearn 直取得を廃止）。5基盤で入力の作り方が違うため。
2. **末尾の GCS / BigQuery 部分を削除**した。流用元ではここが唯一の
   プラットフォーム依存箇所であり、まさにその差し替え点が本プロジェクトの
   比較対象そのもの。artifact の転送は platforms/ の ArtifactStore、
   メトリクス記録は core/telemetry が担う（core/ml にクラウド SDK を持ち込まない）。
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

import lightgbm as lgb
import pandas as pd

from core.ml.config.constants import DATASET_NAME, MODEL_NAME
from core.ml.config.params import coerce_param_types, split_runner_params
from core.ml.config.revision import code_revision
from core.ml.data.load import load_bronze_dataset
from core.ml.data.split import split_train_valid_test
from core.ml.data.validate import validate_silver_dataset
from core.ml.evaluation.baseline import evaluate_baseline
from core.ml.evaluation.explain import build_feature_importance
from core.ml.evaluation.metrics import evaluate_regression
from core.ml.features.build import build_gold_features
from core.ml.registry.artifacts import save_model_artifacts
from core.ml.registry.manifest import (
    build_run_manifest,
    new_run_id,
    write_run_manifest,
)
from core.ml.training.train_lightgbm import train_lightgbm_model


@dataclass(frozen=True)
class TrainingConfig:
    """output_dir は書き込み可能なローカルディレクトリ。

    Tier B（Snowflake sproc / Databricks serverless）には自由に使える作業領域が
    無いため、未指定なら一時ディレクトリへ書く。**artifact を出さない分岐は作らない**
    （出力の有無が基盤で変わると、成果物の比較ができなくなる）。
    転送先（GCS/S3/Blob/Volume/stage）への upload は platforms 側の ArtifactStore。
    """

    output_dir: Path | None = None
    seed: int = 42
    run_id: str | None = None
    params: dict | None = None
    # RandomForest ベースラインを併走させるか（docs/02_architecture.md「共通ML仕様」）。
    # **既定は False。** 5基盤のジョブで毎回2つ学習すると、比較したい実行時間に
    # 無関係な負荷が乗る。Phase 0 のローカル基準確立でだけ有効にする。
    with_baseline: bool = False


@dataclass(frozen=True)
class TrainingResult:
    model: lgb.Booster
    metrics: dict[str, float | int]
    feature_importance: pd.DataFrame
    output_dir: Path
    manifest_path: Path
    run_id: str
    code_revision: str


def run_training_pipeline(df: pd.DataFrame, config: TrainingConfig) -> TrainingResult:
    # code_revision は**学習の前**に解決する。解決できない環境（stamp 忘れの
    # wheel 等）で3分学習してから落とすと、試行時間だけ失われる。
    revision = code_revision()
    resolved_run_id = config.run_id or new_run_id()

    # SageMaker は hyperparameters.json の全値を文字列で渡す。ここで型を戻し、
    # ランナー用（seed / rounds）と LightGBM override に振り分ける。
    params = coerce_param_types(config.params or {})
    runner, overrides = split_runner_params(params)
    seed = int(runner.get("seed", config.seed))

    raw_dataset = load_bronze_dataset(df)
    validate_silver_dataset(raw_dataset)
    feature_table = build_gold_features(raw_dataset)
    split = split_train_valid_test(
        feature_table.features,
        feature_table.target,
        seed=seed,
    )

    model = train_lightgbm_model(
        split,
        seed=seed,
        num_boost_round=int(runner.get("num_boost_round", 2000)),
        early_stopping_rounds=int(runner.get("early_stopping_rounds", 50)),
        overrides=overrides,
    )
    metrics = evaluate_regression(model, split.X_test, split.y_test)
    if config.with_baseline:
        # 健全性チェック（精度の優劣比較は非対象）。キーは baseline_ 接頭辞で分ける
        metrics = {**metrics, **evaluate_baseline(split, seed=seed)}
    importance = build_feature_importance(model, feature_table.schema.columns)

    output_dir = config.output_dir or Path(tempfile.mkdtemp(prefix="mlrun-"))
    save_model_artifacts(
        output_dir=output_dir,
        model=model,
        metrics=metrics,
        feature_importance=importance,
    )
    manifest = build_run_manifest(
        run_id=resolved_run_id,
        code_revision=revision,
        model_name=MODEL_NAME,
        dataset_name=DATASET_NAME,
        seed=seed,
        metrics=metrics,
        params=params,
        artifact_uri=None,  # 転送先は platforms/ の ArtifactStore が決める
        feature_columns=feature_table.schema.columns,
        feature_dtypes=feature_table.schema.dtypes,
    )
    manifest_path = write_run_manifest(output_dir, manifest)

    return TrainingResult(
        model=model,
        metrics=metrics,
        feature_importance=importance,
        output_dir=output_dir,
        manifest_path=manifest_path,
        run_id=resolved_run_id,
        code_revision=revision,
    )
