"""Neon ロード CLI の検証。**実 DB も sklearn も呼ばずに協力者を注入する。**

この CLI は「5基盤への配布物の起点」（`export` が吐く Parquet + checksum）なのに
未検証だった。ここで固定するのは、静かに壊れると原因究明に最も時間を溶かす経路:

  - `load` が **total=0 を成功として返さない**（空のまま学習へ進むと
    5基盤それぞれ別の失敗をして原因が分からなくなる）
  - `export` が **0 行の配布物を作らない**（同上。checksum だけ揃って中身が空）
  - checksum のサイドカーが `<digest>  <filename>` の形式（sha256sum -c 互換）
  - 再実行で inserted=0 になるのは**正常**（冪等）

exit code 規約: 0=成功 / 1=データ異常 / 2=引数不備
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from tests.fakes.telemetry import make_housing_record as record

from platforms.neon import load as cli
from platforms.neon.records import HousingRecord


def frame(rows: int = 2) -> pd.DataFrame:
    return pd.DataFrame(
        [record(i).as_row() for i in range(rows)], columns=list(cli.repository.INSERT_COLUMNS)
    )


# --- create / drop --------------------------------------------------------


def test_create_calls_ddl_and_reports(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[str] = []

    assert cli.cmd_create(create=lambda: calls.append("create")) == cli.EXIT_OK
    assert calls == ["create"]
    assert "created:" in capsys.readouterr().out


def test_drop_calls_ddl_and_reports(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[str] = []

    assert cli.cmd_drop(drop=lambda: calls.append("drop")) == cli.EXIT_OK
    assert calls == ["drop"]
    assert "dropped:" in capsys.readouterr().out


# --- load -----------------------------------------------------------------


def test_load_creates_table_before_inserting() -> None:
    """テーブルが無い状態から1コマンドで通ること（順序が逆だと落ちる）。"""
    order: list[str] = []

    cli.cmd_load(
        fetch=lambda: (order.append("fetch"), [record(0)])[1],
        create=lambda: order.append("create"),
        insert=lambda records: (order.append("insert"), len(records))[1],
        count=lambda: 1,
    )

    assert order == ["create", "fetch", "insert"]


def test_load_reports_skipped_rows_on_rerun(capsys: pytest.CaptureFixture[str]) -> None:
    """再実行で inserted=0 は正常（冪等）。total が入っていれば成功。"""
    result = cli.cmd_load(
        fetch=lambda: [record(0), record(1)],
        create=lambda: None,
        insert=lambda records: 0,
        count=lambda: 20640,
    )

    assert result == cli.EXIT_OK
    assert "inserted=0 skipped=2 total=20640" in capsys.readouterr().out


def test_load_fails_when_table_stays_empty(capsys: pytest.CaptureFixture[str]) -> None:
    """**バグ修正の回帰テスト**: total=0 を成功で返していた。

    空のまま次工程へ進むと、5基盤がそれぞれ別の失敗をして原因が分からなくなる。
    """
    result = cli.cmd_load(
        fetch=lambda: [record(0)],
        create=lambda: None,
        insert=lambda records: 0,
        count=lambda: 0,
    )

    assert result == cli.EXIT_DATA_ERROR
    assert "テーブルが空" in capsys.readouterr().out


# --- export（5基盤への配布物の起点）--------------------------------------


def test_export_writes_parquet_and_checksum_sidecar(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "california_housing.parquet"

    result = cli.cmd_export(output, fetch_frame=lambda: frame(3))

    assert result == cli.EXIT_OK
    assert output.exists()
    sidecar = tmp_path / "california_housing.sha256"
    expected = hashlib.sha256(output.read_bytes()).hexdigest()
    # sha256sum -c が読める形式（<digest>␣␣<filename>）
    assert sidecar.read_text(encoding="utf-8") == f"{expected}  {output.name}\n"
    assert "exported 3 rows" in capsys.readouterr().out


def test_export_roundtrips_through_parquet(tmp_path: Path) -> None:
    """書き出したものが読み戻せて列と行数が保たれること。"""
    output = tmp_path / "data.parquet"

    cli.cmd_export(output, fetch_frame=lambda: frame(2))

    restored = pd.read_parquet(output)
    assert list(restored.columns) == list(cli.repository.INSERT_COLUMNS)
    assert len(restored) == 2


def test_export_refuses_to_write_an_empty_distribution(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """**バグ修正の回帰テスト**: 0 行でも Parquet と checksum を書いていた。

    checksum だけ揃って中身が空の配布物が5基盤へ渡ると、比較の前提が静かに壊れる。
    """
    output = tmp_path / "empty.parquet"

    result = cli.cmd_export(output, fetch_frame=lambda: frame(0))

    assert result == cli.EXIT_DATA_ERROR
    assert not output.exists()
    assert not (tmp_path / "empty.sha256").exists()
    assert "取得件数が 0" in capsys.readouterr().out


# --- read / summary -------------------------------------------------------


def test_read_passes_limit_through(capsys: pytest.CaptureFixture[str]) -> None:
    seen: dict[str, Any] = {}

    def fetch(**kwargs: Any) -> list[HousingRecord]:
        seen.update(kwargs)
        return [record(0)]

    assert cli.cmd_read(3, fetch=fetch) == cli.EXIT_OK
    assert seen == {"limit": 3}
    assert "HousingRecord" in capsys.readouterr().out


def test_summary_prints_every_key(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.cmd_summary(summarize=lambda: {"rows": 20640.0, "mean": 2.06}) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "rows" in out
    assert "mean" in out


# --- 引数 -----------------------------------------------------------------


def test_parser_defaults() -> None:
    parser = cli.build_parser()

    assert parser.parse_args(["read"]).limit == 5
    assert parser.parse_args(["export"]).output == Path("data/california_housing.parquet")
    assert parser.parse_args(["export", "--output", "x.parquet"]).output == Path("x.parquet")


def test_missing_subcommand_is_an_argument_error() -> None:
    """引数不備は exit 2（規約）。argparse に任せている。"""
    with pytest.raises(SystemExit) as excinfo:
        cli.main([])

    assert excinfo.value.code == 2
