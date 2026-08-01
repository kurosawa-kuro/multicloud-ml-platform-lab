"""配布物を各基盤のストレージへ置く（完了条件①と②の間の手作業を無くす）。

`platforms/artifacts.py` の `ArtifactStore` は Phase 1 から実装・テスト済みだったが
**呼び出し口が無く、実行経路のどこからも使われていなかった**。そのため各 runbook の
「§3 配布物の準備（自動化なし・手打ち）」に、基盤ごとに違う `gsutil` / `aws s3 cp` /
`az storage blob upload-batch` が並び、再現性が runbook の読解に依存していた
（SageMaker runbook は「CLI から呼ぶ口が無いので手打ち」と自認していた）。

このスクリプトが埋めるのは **Tier A 3基盤の学習データ配布**だけ。

  - Tier B（Databricks / Snowflake）は adapter 側に upload 実装があり配線済み
    （wheel を Volume へ、zip を stage へ）。二重に口を作らない
  - イメージの push は `make docker-push` が持つ（docker を Python から
    呼び直しても得が無い。SageMaker の manifest 形式だけは例外で、
    そちらは Makefile 側で吸収する）

    doppler run -- python scripts/distribute.py vertex
    doppler run -- python scripts/distribute.py --all

exit code 規約（.claude/rules/scripts.md）: 0=成功 / 1=配布失敗 / 2=引数・設定不備
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from core.telemetry.schemas import Platform
from platforms.shared.artifacts import BlobArtifactStore, GcsArtifactStore, S3ArtifactStore
from platforms.shared.config import ConfigError, load_settings

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2

# 学習データを置ける基盤。Tier B は adapter が自分で運ぶ。
DISTRIBUTABLE: tuple[Platform, ...] = (Platform.VERTEX, Platform.SAGEMAKER, Platform.AZUREML)

DEFAULT_DATASET_DIR = Path("data")
DATASET_PATTERN = "california_housing.*"


def _dataset_files(directory: Path) -> list[Path]:
    found = sorted(directory.glob(DATASET_PATTERN))
    if not found:
        raise ConfigError(
            f"{directory} に {DATASET_PATTERN} が無い。先に `make dataset-export` を実行する"
        )
    return found


def _outputs(stem: str) -> dict[str, Any]:
    path = Path("artifacts") / f"{stem}.outputs.json"
    if not path.exists():
        raise ConfigError(
            f"{path} が無い。`terraform -chdir=infra/environments/{stem} output -json > {path}`"
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        key: entry.get("value") if isinstance(entry, dict) else entry for key, entry in raw.items()
    }


def build_target(platform: Platform, settings: Any) -> tuple[Any, str]:
    """`(ArtifactStore, 配布先 URI)` を返す。

    URI の組み立てが基盤ごとに違うのは、**入れ物の階層が違う**から
    （GCS/S3 はバケット直下、Azure はアカウント > コンテナ > blob）。
    その差は artifacts.py ではなくここで吸収する（store 自体は同じ形に保つ）。
    """
    if platform is Platform.VERTEX:
        from google.cloud import storage  # noqa: PLC0415 - gcp extra

        config = settings.vertex()
        return GcsArtifactStore(storage.Client()), f"gs://{config.bucket}/{config.data_prefix}"

    if platform is Platform.SAGEMAKER:
        import boto3  # noqa: PLC0415 - aws extra

        config = settings.sagemaker()
        return S3ArtifactStore(boto3.client("s3")), f"s3://{config.bucket}/{config.data_prefix}"

    from azure.identity import DefaultAzureCredential  # noqa: PLC0415 - azure extra
    from azure.storage.blob import BlobServiceClient  # noqa: PLC0415

    outputs = _outputs("azure-dev")
    account = outputs["storage_account_name"]
    client = BlobServiceClient(
        f"https://{account}.blob.core.windows.net", credential=DefaultAzureCredential()
    )
    # Workspace 既定データストアのコンテナ名は `azureml-blobstore-<workspace guid>`。
    # 名前は apply が決めるので outputs 経由では取れず、実体から引く。
    container = next(
        name
        for name in (c.name for c in client.list_containers())
        if name.startswith("azureml-blobstore")
    )
    # adapter の data_uri（azureml://.../paths/data/california_housing）と同じ場所を指す
    return BlobArtifactStore(client), f"abfs://{container}/data/california_housing"


def distribute(platform: Platform, dataset_dir: Path, settings: Any) -> str:
    store, remote_uri = build_target(platform, settings)
    _dataset_files(dataset_dir)  # 配る物が無いなら upload 前に落とす
    return store.upload_dir(dataset_dir, remote_uri)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="distribute")
    parser.add_argument(
        "platform",
        nargs="?",
        choices=[p.value for p in DISTRIBUTABLE],
        help="配布先。Tier B は adapter が運ぶのでここには無い",
    )
    parser.add_argument("--all", action="store_true", help="Tier A 3基盤へ配る")
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if bool(args.platform) == bool(args.all):
        print("platform か --all のどちらか一方を指定する", file=sys.stderr)
        return EXIT_USAGE

    targets = DISTRIBUTABLE if args.all else (Platform(args.platform),)
    settings = load_settings()

    failed = 0
    for platform in targets:
        try:
            uri = distribute(platform, args.dataset_dir, settings)
            print(f"[ok]      {platform.value:10} -> {uri}")
        except ConfigError as exc:
            print(f"[config]  {platform.value:10} {exc}", file=sys.stderr)
            return EXIT_USAGE
        except Exception as exc:  # noqa: BLE001 - 1基盤の失敗で残りを止めない
            print(f"[failed]  {platform.value:10} {type(exc).__name__}: {exc}", file=sys.stderr)
            failed += 1

    return EXIT_FAILED if failed else EXIT_OK


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    raise SystemExit(main())
