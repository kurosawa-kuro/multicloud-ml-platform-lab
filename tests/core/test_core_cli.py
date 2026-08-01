"""5基盤共通 CLI（core.ml.cli）の契約。

**これが3基盤の entrypoint シムと Tier B の呼び出しが共有する唯一の入口**なのに
未検証だった。合成 parquet で実際に学習まで走らせる（モックしない）——
CI で学習コードが1行も実行されない状態を解消するのもこのテストの役割。

exit code 規約（シムがそのまま伝播する）:
    0 = 成功 / 1 = 学習失敗 / 2 = 引数・入力契約違反
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from tests.conftest import make_sample_frame

from core.ml.cli.__main__ import main

# 合成 200 行にフル 2000 round は不要。テストを速く保つ
FAST_PARAMS = json.dumps({"num_boost_round": 20, "early_stopping_rounds": 5})


def run_cli(input_dir: Path, output_dir: Path, *extra: str) -> int:
    return main(
        ["--input", str(input_dir), "--output", str(output_dir), "--params", FAST_PARAMS, *extra]
    )


def test_success_writes_artifacts_and_exits_zero(
    sample_parquet: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "model"

    assert run_cli(sample_parquet, output) == 0

    # 4成果物が5基盤共通の出力契約（比較はこのファイル名で突き合わせる）
    for name in ("model.txt", "metrics.json", "feature_importance.csv", "run.json"):
        assert (output / name).exists(), name
    out = capsys.readouterr().out
    # LightGBM の学習ログが JSON の前に混ざるので、最初の "{" から読む
    summary = json.loads(out[out.index("{") :])
    assert summary["rows"] == 200
    assert "rmse" in summary["metrics"]


def test_run_id_is_propagated_to_the_manifest(sample_parquet: Path, tmp_path: Path) -> None:
    """adapter が発番した run_id が成果物側にも残る（run と artifact の照合キー）。"""
    output = tmp_path / "model"

    run_cli(sample_parquet, output, "--run-id", "test-run-42")

    manifest = json.loads((output / "run.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == "test-run-42"


def test_invalid_params_json_is_an_input_error(sample_parquet: Path, tmp_path: Path) -> None:
    assert (
        main(["--input", str(sample_parquet), "--output", str(tmp_path), "--params", "{broken"])
        == 2
    )


def test_params_must_be_a_json_object(sample_parquet: Path, tmp_path: Path) -> None:
    """`--params '[1,2]'` のような配列を黙って無視しない。"""
    assert main(["--input", str(sample_parquet), "--output", str(tmp_path), "--params", "[1]"]) == 2


def test_missing_input_dir_is_an_input_error(tmp_path: Path) -> None:
    assert main(["--input", str(tmp_path / "nowhere"), "--output", str(tmp_path)]) == 2


def test_contract_violation_is_exit_2_not_1(tmp_path: Path) -> None:
    """列が欠けたデータは「学習失敗(1)」ではなく「入力契約違反(2)」。

    1 と 2 が混ざると、failure_class の data / sdk の切り分けがシム側でできない。
    """
    directory = tmp_path / "input"
    directory.mkdir()
    broken = make_sample_frame().drop(columns=["med_inc"])
    broken.to_parquet(directory / "data.parquet", index=False)

    assert main(["--input", str(directory), "--output", str(tmp_path / "out")]) == 2


def test_multiple_parquet_files_are_rejected(tmp_path: Path) -> None:
    """配布物は単一ファイルの契約。複数あると黙ってどれかを読む方が危険。"""
    directory = tmp_path / "input"
    directory.mkdir()
    frame = make_sample_frame()
    frame.to_parquet(directory / "a.parquet", index=False)
    frame.to_parquet(directory / "b.parquet", index=False)

    assert main(["--input", str(directory), "--output", str(tmp_path / "out")]) == 2


def test_saved_model_is_reloadable_and_orders_columns(sample_parquet: Path, tmp_path: Path) -> None:
    """CLI が書いた model.txt を Predictor が読めて、列順が学習時と一致する。

    学習（CLI）→ 配信（core.app）の受け渡しが**この2ファイルの間の契約**。
    """
    import lightgbm as lgb

    from core.app.serving.predictor import Predictor
    from core.ml.config.constants import FEATURE_COLUMNS

    output = tmp_path / "model"
    run_cli(sample_parquet, output)

    booster = lgb.Booster(model_file=str(output / "model.txt"))
    predictor = Predictor(booster)
    assert predictor.feature_names == list(FEATURE_COLUMNS)

    frame = pd.read_parquet(next(sample_parquet.glob("*.parquet")))
    instance = {c: float(frame[c].iloc[0]) for c in FEATURE_COLUMNS}
    shuffled = dict(reversed(list(instance.items())))
    assert predictor.predict([instance]) == predictor.predict([shuffled])
