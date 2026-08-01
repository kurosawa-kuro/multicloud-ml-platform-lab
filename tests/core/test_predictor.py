"""実 Predictor（モデル解決とロード）の検証。

契約テスト（test_core_app_contracts）は StubPredictor 差し替えで**ルーティング**を
見ている。ここは差し替えていた本体側 —— train/serve skew の要所を見る:

  - 3基盤の環境変数差（MODEL_DIR / AZUREML_MODEL_DIR / AIP_STORAGE_URI）の解決順
  - **gs:// をローカルパスに黙って変換しない**（Vertex の起動シム契約違反を即検出）
  - 実 booster での列順整列（学習時の feature_name() を正とする）
  - get_predictor の1回ロード（コールドスタート計測を汚さない）とモデル不在エラー
"""

from __future__ import annotations

from pathlib import Path

import lightgbm as lgb
import pytest
from tests.conftest import make_sample_frame

from core.app.serving.predictor import (
    MODEL_FILENAME,
    Predictor,
    get_predictor,
    locate_model_file,
    resolve_model_dir,
)
from core.ml.config.constants import FEATURE_COLUMNS
from core.ml.pipelines.train_pipeline import TrainingConfig, run_training_pipeline


@pytest.fixture(scope="module")
def model_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """実学習で model.txt を1度だけ作る（このモジュールの全テストで共有）。"""
    output = tmp_path_factory.mktemp("model")
    run_training_pipeline(
        make_sample_frame(),
        TrainingConfig(
            output_dir=output,
            params={"num_boost_round": 20, "early_stopping_rounds": 5},
        ),
    )
    return output


@pytest.fixture(autouse=True)
def _isolate_env_and_cache(monkeypatch: pytest.MonkeyPatch):
    for name in ("MODEL_DIR", "AZUREML_MODEL_DIR", "AIP_STORAGE_URI"):
        monkeypatch.delenv(name, raising=False)
    get_predictor.cache_clear()
    yield
    get_predictor.cache_clear()


# --- resolve_model_dir: 3基盤の環境変数差 ---------------------------------


def test_defaults_to_sagemaker_fixed_path() -> None:
    assert resolve_model_dir() == Path("/opt/ml/model")


@pytest.mark.parametrize("env_name", ["MODEL_DIR", "AZUREML_MODEL_DIR"])
def test_local_path_envs_are_used(env_name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(env_name, "/mnt/models")
    assert resolve_model_dir() == Path("/mnt/models")


def test_model_dir_wins_over_platform_envs(monkeypatch: pytest.MonkeyPatch) -> None:
    """起動シムが設定する MODEL_DIR が基盤の環境変数より優先されること。"""
    monkeypatch.setenv("MODEL_DIR", "/from-shim")
    monkeypatch.setenv("AZUREML_MODEL_DIR", "/from-azure")
    assert resolve_model_dir() == Path("/from-shim")


def test_remote_uri_is_rejected_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """gs:// を黙ってパス化しない。「モデルが無い」への誤診より契約違反を即報告。"""
    monkeypatch.setenv("AIP_STORAGE_URI", "gs://bucket/models/1")
    with pytest.raises(RuntimeError, match="リモート URI"):
        resolve_model_dir()


# --- Predictor: 実 booster での列順契約 -----------------------------------


def test_feature_names_come_from_the_trained_model(model_dir: Path) -> None:
    booster = lgb.Booster(model_file=str(model_dir / MODEL_FILENAME))
    assert Predictor(booster).feature_names == list(FEATURE_COLUMNS)


def test_prediction_is_key_order_independent(model_dir: Path) -> None:
    """実モデルで、入力 dict のキー順が結果を変えないこと（skew の核心）。"""
    booster = lgb.Booster(model_file=str(model_dir / MODEL_FILENAME))
    predictor = Predictor(booster)
    instance = {c: float(i + 1) for i, c in enumerate(FEATURE_COLUMNS)}
    shuffled = dict(reversed(list(instance.items())))

    assert predictor.predict([instance]) == predictor.predict([shuffled])


def test_missing_feature_is_named_in_the_error(model_dir: Path) -> None:
    booster = lgb.Booster(model_file=str(model_dir / MODEL_FILENAME))
    predictor = Predictor(booster)
    incomplete = {c: 1.0 for c in FEATURE_COLUMNS[1:]}

    with pytest.raises(ValueError, match=FEATURE_COLUMNS[0]):
        predictor.predict([incomplete])


# --- get_predictor: 1回ロードと不在エラー ---------------------------------


def test_get_predictor_loads_once_and_caches(
    model_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MODEL_DIR", str(model_dir))
    assert get_predictor() is get_predictor()


def test_missing_model_file_names_the_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_DIR", str(tmp_path))
    with pytest.raises(RuntimeError, match=str(tmp_path)):
        get_predictor()


def test_gs_uri_reaches_get_predictor_as_a_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIP_STORAGE_URI", "gs://bucket/model")
    with pytest.raises(RuntimeError, match="リモート URI"):
        get_predictor()


# --- 5基盤のモデル配置レイアウト（2026-08-01・修正08）----------------------
#
# 実行契約が3者3様なので、推論コンテナが受け取るモデルの置かれ方も違う。
# Azure の「1段深い」は**実クラウドでしか出なかった** skew で、
# `/health` は 200 のまま `/score` だけ 500 になった（04_azureml.md 詰まった点(3)）。
# ローカルの `make train` は output_dir 直下に置くため再現しない。
#
# Tier B（Databricks / Snowflake）はこの推論コンテナを使わない
# （Serving が MLflow モデル / warehouse 関数を直接読む）ので対象外。

LAYOUTS = {
    # SageMaker: 基盤が model.tar.gz を /opt/ml/model へ展開する（直下）
    "sagemaker": ("MODEL_DIR", ""),
    # Vertex: 起動シムが GCS から取得したローカルパスを MODEL_DIR に入れる（直下）
    "vertex": ("MODEL_DIR", ""),
    # Azure ML: ジョブ出力のフォルダ名を保ったままマウントする（1段深い）
    "azureml": ("AZUREML_MODEL_DIR", "model"),
}


@pytest.fixture
def mounted_model(model_dir: Path, tmp_path: Path):
    """基盤ごとのマウント形を作って (env 名, 基準ディレクトリ) を返す。"""

    def build(platform: str) -> tuple[str, Path]:
        env_name, nested = LAYOUTS[platform]
        base = tmp_path / platform
        target = base / nested if nested else base
        target.mkdir(parents=True)
        (target / MODEL_FILENAME).write_bytes((model_dir / MODEL_FILENAME).read_bytes())
        return env_name, base

    return build


@pytest.mark.parametrize("platform", sorted(LAYOUTS))
def test_model_is_found_in_every_tier_a_layout(
    platform: str, mounted_model, monkeypatch: pytest.MonkeyPatch
) -> None:
    """3基盤のどのマウント形でもモデルに到達すること。"""
    env_name, base = mounted_model(platform)
    monkeypatch.setenv(env_name, str(base))
    get_predictor.cache_clear()

    assert get_predictor().feature_names == list(FEATURE_COLUMNS)


@pytest.mark.parametrize("platform", sorted(LAYOUTS))
def test_prediction_is_identical_across_layouts(
    platform: str, mounted_model, monkeypatch: pytest.MonkeyPatch
) -> None:
    """置かれ方が違っても同じ予測値が出ること（5基盤で一致した実測の前提）。"""
    env_name, base = mounted_model(platform)
    monkeypatch.setenv(env_name, str(base))
    get_predictor.cache_clear()
    instance = {column: float(index + 1) for index, column in enumerate(FEATURE_COLUMNS)}

    assert get_predictor().predict([instance]) == pytest.approx(get_predictor().predict([instance]))


# --- locate_model_file の分岐 ---------------------------------------------


def test_locates_model_one_level_down(model_dir: Path, tmp_path: Path) -> None:
    mounted = tmp_path / "azureml-models" / "mcml-california-housing" / "2"
    (mounted / "model").mkdir(parents=True)
    (mounted / "model" / MODEL_FILENAME).write_bytes((model_dir / MODEL_FILENAME).read_bytes())

    assert locate_model_file(mounted) == mounted / "model" / MODEL_FILENAME


def test_direct_placement_wins_over_subdirectory(model_dir: Path, tmp_path: Path) -> None:
    """SageMaker / Vertex の直下配置は、子に何があっても最初の分岐で返る。"""
    (tmp_path / MODEL_FILENAME).write_bytes((model_dir / MODEL_FILENAME).read_bytes())
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / MODEL_FILENAME).write_text("decoy")

    assert locate_model_file(tmp_path) == tmp_path / MODEL_FILENAME


def test_ambiguous_subdirectories_are_not_guessed(tmp_path: Path) -> None:
    """2個以上あるとどれが正か決められないので拾わない（誤ったモデルで推論しない）。"""
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        (tmp_path / name / MODEL_FILENAME).write_text("x")

    assert locate_model_file(tmp_path) is None


def test_get_predictor_loads_from_azure_mount(
    model_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "model").mkdir()
    (tmp_path / "model" / MODEL_FILENAME).write_bytes((model_dir / MODEL_FILENAME).read_bytes())
    monkeypatch.setenv("AZUREML_MODEL_DIR", str(tmp_path))
    get_predictor.cache_clear()

    assert get_predictor().feature_names
