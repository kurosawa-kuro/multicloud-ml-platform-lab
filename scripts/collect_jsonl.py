"""fallback 収集: JSONL の ml_runs を Neon へ流し込む（`make collect`）。

ジョブ内から Neon へ直接 INSERT できなかった場合、計測は JSONL
（core.telemetry.sinks.JsonlRunSink）として各基盤ストレージに落ちている。
それをローカルへ回収したあと、本スクリプトが Neon へ入れ直す。

**到達できなかったこと自体が結果**なので、回収行は write_path='collected'
のまま入れる（JsonlRunSink が書いた時点で確定済み）。黙って direct に
すり替えると「Neon 到達可否」という比較軸が消える。

run_id は uuid の主キーなので再実行しても重複しない（on conflict do nothing）。

    doppler run -- python scripts/collect_jsonl.py --from-dir artifacts/fallback

各基盤ストレージからのダウンロード（--platform）は各 Phase の adapter 実装と
対で入れる。ここで先回りすると未検証のクラウド呼び出しが増えるだけなので stub。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from core.telemetry.schemas import WritePath
from core.telemetry.sinks import JSONL_FILENAME, record_to_run


def load_dir_to_neon(directory: Path, *, sink: Any = None) -> tuple[int, int]:
    """ディレクトリ配下の ml_runs JSONL を全て Neon へ入れる。(挿入, スキップ)。

    sink は注入可能（既定は NeonRunSink）。関数内で直接生成すると
    実 DB 無しで1行も検証できない（run_sink / repository と同じ理由）。
    """
    paths = sorted(directory.rglob(JSONL_FILENAME))
    if not paths:
        raise FileNotFoundError(f"{directory} に {JSONL_FILENAME} がありません")

    if sink is None:
        from platforms.neon.run_sink import NeonRunSink

        sink = NeonRunSink()
    inserted = 0
    skipped = 0
    for path in paths:
        with path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                run = record_to_run(json.loads(line))
                if sink.insert_run(run, write_path=WritePath.COLLECTED):
                    inserted += 1
                else:
                    skipped += 1
    return inserted, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="collect_jsonl")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--from-dir", type=Path, help="回収済み JSONL のディレクトリ")
    group.add_argument(
        "--platform",
        choices=["vertex", "sagemaker", "azureml", "databricks", "snowflake"],
        help="基盤ストレージから直接回収（各 Phase の adapter と対で実装）",
    )
    args = parser.parse_args(argv)

    if args.platform:
        print(f"未実装: {args.platform} からの直接回収は該当 Phase で実装", file=sys.stderr)
        return 2

    try:
        inserted, skipped = load_dir_to_neon(args.from_dir)
    except FileNotFoundError as exc:
        print(f"入力エラー: {exc}", file=sys.stderr)
        return 2

    print(f"collected: inserted={inserted} skipped(duplicate)={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
