"""RandomForest ベースライン（docs/02_architecture.md「共通ML仕様」）。

**精度の優劣比較は非対象**（docs/01_requirements.md）。ここでの用途は1つだけ:

    LightGBM の結果が「学習できている」と言えるかの粗い健全性チェック。
    同じ分割・同じ seed で RF と並べ、桁違いに悪ければ配管が壊れている
    （列の取り違え・目的変数の混入・分割のずれ）。

そのため **既定では走らせない**。5基盤のジョブで毎回2つのモデルを学習すると、
比較したい実行時間に無関係な負荷が乗る。Phase 0 のローカル基準確立
（`make train BASELINE=1`）でだけ使う。

依存は scikit-learn のみ（既に core/ml の依存にある。増やさない）。
"""

from __future__ import annotations

import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from core.ml.data.split import DatasetSplit

# 比較用ではなく健全性チェックなので、木の本数は控えめでよい。
DEFAULT_ESTIMATORS = 100


def evaluate_baseline(
    split: DatasetSplit,
    *,
    seed: int,
    n_estimators: int = DEFAULT_ESTIMATORS,
) -> dict[str, float]:
    """RF を同じ分割で学習し、test のメトリクスを返す。

    キーに `baseline_` を付けるのは、`ml_runs.metrics` で LightGBM 側の
    rmse / mae / r2 と混ざらないようにするため（比較 SELECT は前者だけを見る）。
    """
    model = RandomForestRegressor(n_estimators=n_estimators, random_state=seed)
    model.fit(split.X_train, split.y_train)
    predicted = model.predict(split.X_test)

    return {
        "baseline_rmse": float(np.sqrt(mean_squared_error(split.y_test, predicted))),
        "baseline_mae": float(mean_absolute_error(split.y_test, predicted)),
        "baseline_r2": float(r2_score(split.y_test, predicted)),
    }
