"""California Housing を Neon にロードする CLI。

    doppler run -- python -m platforms.neon.load create          テーブル作成
    doppler run -- python -m platforms.neon.load load            sklearn から取得して投入
    doppler run -- python -m platforms.neon.load read --limit 5  読み出し確認
    doppler run -- python -m platforms.neon.load summary         行数と目的変数の要約
    doppler run -- python -m platforms.neon.load drop            テーブル削除
    doppler run -- python -m platforms.neon.load export          配布用 Parquet + checksum

load は冪等（既存 row_id はスキップ）なので、途中で切れても再実行してよい。

各コマンドは協力者（DDL / repository / sklearn 取得）を **引数で受け取る**。
モジュールを直接呼ぶ形だと実 DB 無しでは1行も検証できず、
「5基盤への配布物の起点」が無検証のまま残る（tests/test_neon_load_cli.py）。

exit code 規約（.claude/rules/scripts.md）: 0=成功 / 1=データ異常 / 2=引数不備
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from platforms.neon import repository, schema
from platforms.neon.records import DATASET_SOURCE, HousingRecord

EXIT_OK = 0
EXIT_DATA_ERROR = 1


def fetch_records() -> list[HousingRecord]:
    """sklearn から取得して HousingRecord の列に落とす。

    sklearn を import するのはこの関数だけ。Neon 側のモジュールを
    ML 依存で汚さないため、呼ばれるまで import を遅延する。
    """
    from sklearn.datasets import fetch_california_housing

    bunch = fetch_california_housing(as_frame=True)
    frame = bunch.frame
    return [
        HousingRecord(
            row_id=int(idx),
            med_inc=float(row.MedInc),
            house_age=float(row.HouseAge),
            ave_rooms=float(row.AveRooms),
            ave_bedrms=float(row.AveBedrms),
            population=float(row.Population),
            ave_occup=float(row.AveOccup),
            latitude=float(row.Latitude),
            longitude=float(row.Longitude),
            med_house_val=float(row.MedHouseVal),
        )
        for idx, row in frame.iterrows()
    ]


def cmd_create(*, create: Callable[[], None] = schema.create_table) -> int:
    create()
    print(f"created: {schema.TABLE_NAME}")
    return EXIT_OK


def cmd_drop(*, drop: Callable[[], None] = schema.drop_table) -> int:
    drop()
    print(f"dropped: {schema.TABLE_NAME}")
    return EXIT_OK


def cmd_load(
    *,
    fetch: Callable[[], Sequence[HousingRecord]] = fetch_records,
    create: Callable[[], None] = schema.create_table,
    insert: Callable[[Sequence[HousingRecord]], int] = repository.insert_many,
    count: Callable[[], int] = repository.count,
) -> int:
    """取得 → 投入 → 件数確認。**空のテーブルを成功として返さない**。

    再実行で inserted=0 になるのは正常（冪等）だが、total=0 は
    「入っていない」ので異常。ここを 0 で通すと、後続の学習が
    空データで走って原因不明のメトリクス不一致になる。
    """
    create()
    records = fetch()
    print(f"fetched {len(records)} rows from {DATASET_SOURCE}")

    inserted = insert(records)
    total = count()
    print(f"inserted={inserted} skipped={len(records) - inserted} total={total}")

    if total == 0:
        print("load: テーブルが空のまま（投入が反映されていない）", flush=True)
        return EXIT_DATA_ERROR
    return EXIT_OK


def cmd_export(
    output: Path,
    *,
    fetch_frame: Callable[[], Any] = repository.fetch_dataframe,
) -> int:
    """Neon から単一 Parquet を書き出し、checksum を記録する。

    ここが5基盤への配布物の起点。checksum は code_revision がコードの
    同一性を担保するのと同じ役割をデータで果たす。

    **空の DataFrame は書き出さない**。0 行の Parquet を配ると5基盤が
    それぞれ別の失敗をし、原因究明に時間を溶かす（比較の前提が静かに壊れる）。
    """
    from core.ml.data import load as dataset

    df = fetch_frame()
    if len(df) == 0:
        print("export: 取得件数が 0。先に `load` を実行する（空の配布物は作らない）")
        return EXIT_DATA_ERROR

    path = dataset.to_parquet(df, output)
    digest = dataset.checksum(path)

    (path.parent / f"{path.stem}.sha256").write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    print(f"exported {len(df)} rows -> {path}")
    print(f"sha256   {digest}")
    return EXIT_OK


def cmd_read(
    limit: int,
    *,
    fetch: Callable[..., Sequence[HousingRecord]] = repository.fetch_all,
) -> int:
    for record in fetch(limit=limit):
        print(record)
    return EXIT_OK


def cmd_summary(
    *,
    summarize: Callable[[], dict[str, float]] = repository.summary,
) -> int:
    for key, value in summarize().items():
        print(f"{key:6} {value}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="platforms.neon.load")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("create")
    sub.add_parser("drop")
    sub.add_parser("load")
    sub.add_parser("summary")
    read = sub.add_parser("read")
    read.add_argument("--limit", type=int, default=5)
    export = sub.add_parser("export")
    export.add_argument("--output", type=Path, default=Path("data/california_housing.parquet"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    match args.command:
        case "create":
            return cmd_create()
        case "drop":
            return cmd_drop()
        case "load":
            return cmd_load()
        case "read":
            return cmd_read(args.limit)
        case "summary":
            return cmd_summary()
        case "export":
            return cmd_export(args.output)
    return 2  # pragma: no cover - argparse が未知のコマンドを弾く


if __name__ == "__main__":
    raise SystemExit(main())
