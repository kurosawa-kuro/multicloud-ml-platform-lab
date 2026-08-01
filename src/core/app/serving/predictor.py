"""モデルのロードと推論。

流用元: private-ops starter-kit `api-fastapi/src/micropost_api/db.py` の
「起動時に1度だけ初期化し、Depends で注入する」構造を踏襲している
（DB セッションの代わりにモデルを持つ）。

モデルの所在は基盤ごとに環境変数が違う。**core が引き受けるのはローカルパスの
解決だけ**で、リモート取得はしない:

    SageMaker  /opt/ml/model        （固定パス。基盤が S3 から展開済み）
    Azure ML   AZUREML_MODEL_DIR    （基盤がマウント済み）
    Vertex AI  AIP_STORAGE_URI が **gs:// のまま渡ってくる**。Vertex の予測
               コンテナに FUSE マウントは無いため、起動シム（platforms/vertex
               側）が GCS からローカルへ取得し MODEL_DIR を設定する契約。
               core に GCS クライアントを入れると依存最小が崩れるので、
               gs:// を見たら黙って変換せず**即エラー**にする。

学習時と同じ前処理（列順の固定）を再適用するため、run.json の
preprocess_state ではなく model 側の feature_name() を正とする。
LightGBM が学習時の列順を保持しているので、これが最も壊れにくい。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import lightgbm as lgb
import pandas as pd

MODEL_FILENAME = "model.txt"


def resolve_model_dir() -> Path:
    """3基盤の環境変数差をここだけで吸収する（ローカルパス限定）。"""
    for env_name in ("MODEL_DIR", "AZUREML_MODEL_DIR", "AIP_STORAGE_URI"):
        value = os.getenv(env_name)
        if not value:
            continue
        if "://" in value:
            # gs:// 等のリモート URI を黙ってパスに変換しない。存在しない
            # /gcs/... を作って「モデルが無い」と誤診させるより、契約違反を
            # その場で言う方が Phase 1 のデバッグが速い。
            raise RuntimeError(
                f"{env_name} がリモート URI です（{value}）。起動シムが"
                f"ローカルへ取得して MODEL_DIR を設定してください"
            )
        return Path(value)
    return Path("/opt/ml/model")  # SageMaker の固定パス


def locate_model_file(base: Path) -> Path | None:
    """`base` 直下、無ければ子ディレクトリ1段だけ探して `model.txt` を返す。

    **Azure ML はジョブ出力のフォルダ名を保ったままマウントする**。
    `azureml://jobs/<job>/outputs/model` を登録すると、コンテナ側では
    `$AZUREML_MODEL_DIR/model/model.txt` になり、直下には無い（2026-08-01 実測）。
    SageMaker（`/opt/ml/model` 直下に展開）と Vertex（シムが MODEL_DIR を直接指す）は
    直下に来るので、その2基盤では最初の分岐で返って挙動は変わらない。

    再帰はしない。深く掘ると別モデルの `model.txt` を拾う危険があるうえ、
    1段で足りることが3基盤で確認できているため。
    """
    direct = base / MODEL_FILENAME
    if direct.exists():
        return direct
    if not base.is_dir():
        return None
    nested = sorted(child / MODEL_FILENAME for child in base.iterdir() if child.is_dir())
    found = [p for p in nested if p.exists()]
    if len(found) == 1:
        return found[0]
    return None  # 0個は未配置、2個以上はどれが正か決められないので呼び出し元で落とす


class Predictor:
    """1つの booster を持ち、3契約すべてがこれを共有する。"""

    def __init__(self, booster: lgb.Booster) -> None:
        self._booster = booster
        self._feature_names: list[str] = list(booster.feature_name())

    @property
    def feature_names(self) -> list[str]:
        return list(self._feature_names)

    def predict(self, instances: list[dict[str, Any]]) -> list[float]:
        """学習時の列順に整えてから推論する。

        入力 dict のキー順に依存すると、呼び出し元（3基盤のクライアント）ごとに
        特徴量の並びが変わって別の予測値が出る。
        """
        frame = pd.DataFrame(instances)
        missing = [c for c in self._feature_names if c not in frame.columns]
        if missing:
            raise ValueError(f"特徴量が不足しています: {missing}")
        ordered = frame[self._feature_names]
        return [float(v) for v in self._booster.predict(ordered)]


@lru_cache(maxsize=1)
def get_predictor() -> Predictor:
    """起動後の初回リクエストで1度だけロードする。

    リクエストごとにロードするとコールドスタート計測が汚れる。
    """
    base = resolve_model_dir()
    model_path = locate_model_file(base)
    if model_path is None:
        raise RuntimeError(f"モデルが見つかりません: {base}（直下と子ディレクトリ1段を探索）")
    return Predictor(lgb.Booster(model_file=str(model_path)))
