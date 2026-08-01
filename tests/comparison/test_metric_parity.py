"""metric parity test: 全基盤で RMSE が一致すること。

同一データ・同一SHA・同一seed なら、5基盤で同じ RMSE が出るはず。
出ないなら基盤の性能差ではなく、次のいずれかが起きている:

  - データ層の差（Snowflake で Kaggle 版 California Housing を掴んだ等）
  - パッケージ版の差（Snowflake warehouse は Anaconda channel 限定、
    Databricks は ML Runtime プリインストール版と wheel 依存が衝突しうる）
  - seed / 分割の差

つまりこのテストは精度を測るものではなく、**比較の前提が壊れていないこと**の
検出器。docs/07_test_strategy.md の位置づけに従う。

パイプライン自体の出力契約・再現性は tests/test_train_pipeline.py（合成データ・
常時実行）。ここに残るのは **実配布データが本当に必要なもの**だけ。

基準線: kaggle-bronze-gcp 実測の LGBM RMSE 0.44498 / CatBoost 0.44448
（ML/kaggle-bronze-gcp/docs/competitions/california_housing.md）
"""

from __future__ import annotations

import pandas as pd
import pytest
from tests.conftest import REPO_ROOT
from tests.platform_runs import EXPECTED_PLATFORMS, load_runs

# Phase 0 ローカル実測（2026-07-31・lightgbm 4.6.0 / scikit-learn 1.9.0）。
# 5基盤はこの値と一致することが期待される。
PHASE0_LOCAL_RMSE = 0.4368055090296257

# 同一環境なら完全一致するが、基盤間は BLAS 実装差で最下位桁が揺れうる。
RMSE_TOLERANCE = 1e-6

# kaggle-bronze-gcp の独立実装による実測値。分割条件が異なるため一致はしない。
# 桁が合っていることの sanity check にのみ使う。
REFERENCE_RMSE = 0.44498


@pytest.fixture(scope="module")
def distribution_frame() -> pd.DataFrame:
    """配布用 Parquet（5基盤へ配る実物）。無い環境では module ごと skip。"""
    from core.ml.data.load import read_parquet

    parquet = REPO_ROOT / "data" / "california_housing.parquet"
    if not parquet.exists():
        pytest.skip("data/california_housing.parquet が無い（make dataset-export）")
    return read_parquet(parquet)


def test_rmse_matches_across_platforms() -> None:
    """全基盤の RMSE が RMSE_TOLERANCE 以内で一致すること。"""
    runs = load_runs()
    values = {run.platform: run.rmse for run in runs}
    spread = max(values.values()) - min(values.values())

    assert spread < RMSE_TOLERANCE, f"基盤間で RMSE が乖離: {values}"


def test_rmse_matches_the_local_baseline() -> None:
    """5基盤の RMSE が Phase 0 のローカル基準値と一致すること。

    基盤間で揃っていても、5つ揃って基準値からずれていれば
    「同じデータ・同じコード」の前提が壊れている。
    """
    for run in load_runs():
        assert abs(run.rmse - PHASE0_LOCAL_RMSE) < RMSE_TOLERANCE, (
            f"{run.platform} が基準値から乖離: {run.rmse} vs {PHASE0_LOCAL_RMSE}"
        )


def test_metrics_present_for_every_platform() -> None:
    """各基盤に success の train run が最低1件あること。

    片方が欠けたまま「一致した」と判定されるのを防ぐ
    （比較していないものを比較したことにしない）。
    """
    expected = set(EXPECTED_PLATFORMS)
    recorded = {run.platform for run in load_runs()}

    assert recorded == expected, (
        f"比較対象が揃っていない: 不足={expected - recorded} / 余分={recorded - expected}"
    )


def test_write_path_is_recorded_for_every_platform() -> None:
    """到達経路が全基盤に記録されていること（direct / collected のどちらか）。

    write_path が欠けると「Neon へ届いたか」という比較軸が空になる。
    到達できなかったこと自体が結果なので、null を許さない。
    """
    for run in load_runs():
        assert run.write_path in {"direct", "collected"}, (
            f"{run.platform} の write_path が不正: {run.write_path!r}"
        )


def test_local_baseline_is_reproducible(distribution_frame: pd.DataFrame) -> None:
    """Phase 0 のローカル基準値が再現すること。5基盤の比較対象となる基準線。"""
    from core.ml.pipelines.train_pipeline import TrainingConfig, run_training_pipeline

    first = run_training_pipeline(distribution_frame, TrainingConfig()).metrics
    second = run_training_pipeline(distribution_frame, TrainingConfig()).metrics

    assert first == second, "同一入力・同一seed で結果が揺れている"
    assert abs(first["rmse"] - PHASE0_LOCAL_RMSE) < RMSE_TOLERANCE, (
        f"Phase 0 基準値から乖離: {first['rmse']} vs {PHASE0_LOCAL_RMSE}"
    )
    # 独立実装（kaggle-bronze）と桁が合っていることの sanity check
    assert abs(first["rmse"] - REFERENCE_RMSE) < 0.05
