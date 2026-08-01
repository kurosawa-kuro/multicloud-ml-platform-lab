"""fallback 収集（`make collect`）の検証。**実 Neon を叩かず sink を注入する。**

このスクリプトは「Neon 到達可否」という比較軸の**半分**（collected 側）を担うのに
未検証だった。守る不変条件:

  - 回収行は write_path='collected' のまま入れる（direct にすり替えると
    到達経路の比較が消える）
  - run_id 主キーの重複は挿入 0 = skipped として数える（再実行が安全）
  - JSONL が1つも無いのは入力エラー（exit 2）。「0件回収の成功」と混ぜない
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.conftest import load_script
from tests.fakes.telemetry import make_run

from core.telemetry.schemas import MlRun, Platform, WritePath
from core.telemetry.sinks import JSONL_FILENAME, JsonlRunSink, run_to_record

collect = load_script("collect_jsonl")


class SpySink:
    """insert_run の呼び出しを記録する。重複 run_id は 0 を返す（Neon の on conflict 相当）。"""

    def __init__(self) -> None:
        self.inserted: list[tuple[MlRun, WritePath | None]] = []
        self.seen: set[str] = set()

    def insert_run(self, run: MlRun, *, write_path: WritePath | None = None) -> int:
        self.inserted.append((run, write_path))
        if run.run_id in self.seen:
            return 0
        self.seen.add(run.run_id)
        return 1


def write_jsonl(directory: Path, *runs: MlRun) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / JSONL_FILENAME
    with path.open("a", encoding="utf-8") as f:
        for run in runs:
            f.write(json.dumps(run_to_record(run)) + "\n")
    return path


def test_collected_rows_keep_their_write_path(tmp_path: Path) -> None:
    """すり替え禁止の核心。JSONL 由来は必ず collected で入る。"""
    write_jsonl(tmp_path, make_run(write_path=WritePath.COLLECTED))
    sink = SpySink()

    inserted, skipped = collect.load_dir_to_neon(tmp_path, sink=sink)

    assert (inserted, skipped) == (1, 0)
    _, write_path = sink.inserted[0]
    assert write_path is WritePath.COLLECTED


def test_duplicate_run_ids_are_counted_as_skipped(tmp_path: Path) -> None:
    """再実行しても二重挿入にならない（run_id 主キー + on conflict）。"""
    run = make_run(run_id="11111111-1111-1111-1111-111111111111")
    write_jsonl(tmp_path / "a", run)
    write_jsonl(tmp_path / "b", run)  # 別基盤から同じ run を回収したケース

    inserted, skipped = collect.load_dir_to_neon(tmp_path, sink=SpySink())

    assert (inserted, skipped) == (1, 1)


def test_recursively_collects_from_subdirectories(tmp_path: Path) -> None:
    """基盤ごとのサブディレクトリ（vertex/ sagemaker/ ...）をまとめて回収する。"""
    write_jsonl(tmp_path / "vertex", make_run(run_id="a" * 32, platform=Platform.VERTEX))
    write_jsonl(tmp_path / "snowflake", make_run(run_id="b" * 32, platform=Platform.SNOWFLAKE))

    inserted, _ = collect.load_dir_to_neon(tmp_path, sink=SpySink())

    assert inserted == 2


def test_missing_jsonl_raises_instead_of_reporting_zero(tmp_path: Path) -> None:
    """「回収 0 件の成功」と「回収対象が無い」を混ぜない。"""
    with pytest.raises(FileNotFoundError):
        collect.load_dir_to_neon(tmp_path, sink=SpySink())


def test_roundtrip_from_real_jsonl_sink(tmp_path: Path) -> None:
    """JsonlRunSink が書いた実ファイルをそのまま読めること（形式の整合）。"""
    jsonl = JsonlRunSink(tmp_path)
    jsonl.record_run(make_run(run_id="c" * 32))
    sink = SpySink()

    inserted, _ = collect.load_dir_to_neon(tmp_path, sink=sink)

    assert inserted == 1
    run, _ = sink.inserted[0]
    assert run.write_path is WritePath.COLLECTED  # JsonlRunSink が確定済み


def test_cli_platform_download_is_explicitly_unimplemented() -> None:
    """--platform は各 Phase の adapter と対で実装する（先回りしない）契約。"""
    assert collect.main(["--platform", "vertex"]) == 2
