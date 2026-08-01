"""学習パイプライン（run_training_pipeline）の統合検証。**合成データで常に走る。**

旧 test_metric_parity.py はこれらを実 parquet 依存にしていたため、
parquet の無い環境（CI）では**学習コードが1行も実行されなかった**。
実データが要るのは Phase 0 基準値の照合だけ（test_metric_parity.py に残置）で、
出力契約・切り詰め・型復元・再現性は合成データで検証できる。

パイプライン実行は module スコープの fixture で共有する（同じ設定の再実行を
テストごとに繰り返さない。旧版はパイプラインを6回回していた）。
"""

from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import pandas as pd
import pytest
from tests.conftest import make_sample_frame

from core.ml.pipelines.train_pipeline import TrainingConfig, TrainingResult, run_training_pipeline

FAST = {"num_boost_round": 60, "early_stopping_rounds": 10}


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    return make_sample_frame()


@pytest.fixture(scope="module")
def result(frame: pd.DataFrame) -> TrainingResult:
    """既定設定での1回の実行結果。読み取り専用で共有する。"""
    return run_training_pipeline(frame, TrainingConfig(params=dict(FAST)))


def test_artifacts_are_written_even_without_output_dir(result: TrainingResult) -> None:
    """Tier B（作業ディレクトリを持たない）でも4成果物が出ること。

    出力の有無が基盤で変わると、成果物の比較ができなくなる。
    """
    assert result.output_dir.exists()
    for name in ("model.txt", "metrics.json", "feature_importance.csv", "run.json"):
        assert (result.output_dir / name).exists(), name


def test_manifest_carries_code_revision(result: TrainingResult) -> None:
    """artifact 側にも code_revision が残ること（run とコードの照合に必要）。"""
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["code_revision"] == result.code_revision
    assert len(manifest["code_revision"]) >= 7


def test_saved_model_is_truncated_to_best_iteration(result: TrainingResult) -> None:
    """保存モデルが best_iteration で切られていること。

    全イテレーション保存だと、評価は best_iteration・serving は全木で予測し、
    学習時メトリクスとエンドポイント応答が一致しなくなる（train/serve skew）。
    """
    loaded = lgb.Booster(model_file=str(result.output_dir / "model.txt"))
    assert loaded.num_trees() == result.metrics["best_iteration"]


def test_metrics_include_the_comparison_keys(result: TrainingResult) -> None:
    """rmse / mae / r2 が5基盤の比較キー。1つでも欠けると SELECT が崩れる。"""
    for key in ("rmse", "mae", "r2", "best_iteration"):
        assert key in result.metrics, key


def test_rerun_with_same_seed_is_identical(frame: pd.DataFrame, result: TrainingResult) -> None:
    """同一入力・同一 seed で完全一致（metric parity の土台）。"""
    again = run_training_pipeline(frame, TrainingConfig(params=dict(FAST)))
    assert again.metrics == result.metrics


def test_string_params_match_native_params(frame: pd.DataFrame) -> None:
    """SageMaker（文字列）と Tier B（native）で同一パラメータなら同一メトリクス。"""
    as_strings = run_training_pipeline(
        frame,
        TrainingConfig(params={k: str(v) for k, v in FAST.items()} | {"learning_rate": "0.05"}),
    ).metrics
    as_native = run_training_pipeline(
        frame, TrainingConfig(params=dict(FAST) | {"learning_rate": 0.05})
    ).metrics
    assert as_strings == as_native


def test_baseline_is_off_by_default(result: TrainingResult) -> None:
    """5基盤のジョブで RF を毎回学習しない（比較したい実行時間に無関係な負荷）。"""
    assert not any(key.startswith("baseline_") for key in result.metrics)


def test_baseline_runs_when_requested(frame: pd.DataFrame) -> None:
    """Phase 0 の健全性チェック。LightGBM 側のキーと混ざらないこと。"""
    metrics = run_training_pipeline(
        frame, TrainingConfig(params=dict(FAST), with_baseline=True)
    ).metrics

    assert {"rmse", "mae", "r2"} <= set(metrics)
    assert {"baseline_rmse", "baseline_mae", "baseline_r2"} <= set(metrics)
    # 桁違いに悪ければ配管が壊れている（列の取り違え・目的変数の混入・分割のずれ）
    assert metrics["baseline_rmse"] < metrics["rmse"] * 10


def test_explicit_output_dir_is_respected(frame: pd.DataFrame, tmp_path: Path) -> None:
    """Tier A（出力パスが契約で決まる）では指定先へ書くこと。"""
    target = tmp_path / "model"
    outcome = run_training_pipeline(frame, TrainingConfig(output_dir=target, params=dict(FAST)))
    assert outcome.output_dir == target
    assert (target / "model.txt").exists()
