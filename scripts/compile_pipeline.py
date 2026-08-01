"""Vertex AI Pipelines の定義をコンパイルする（投入はしない）。

    doppler run -- python scripts/compile_pipeline.py

**クラウドを叩かない。** 生成物は `artifacts/vertex-pipeline.yaml`。
投入は owner 承認の対象なので、ここには入れない（修正11 のタスクノート参照）。

⚠️ **生成物には Neon 接続文字列が平文で載る**（ステップの env として焼き込まれるため）。
`artifacts/` は .gitignore 済みだが、**コミット・共有・貼り付けをしない**こと。
投入後は Vertex 側のジョブ定義からも読めるようになる —— これは
`contracts.tracking.telemetry_env` が既に負っているのと同じリスクで、
本ラボの範囲では許容、本番設計では secret manager 参照に置き換える。

設定は既存の解決順（`platforms.shared.config`）から引き、ステップコンテナには
`MCML_VERTEX_<FIELD>` として渡す。terraform outputs はコンテナ内に無いため、
**ローカルで解決した値を env に載せ替える**のがこのスクリプトの仕事。

exit code 規約（.claude/rules/scripts.md）: 0=成功 / 2=設定不備
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from core.telemetry.schemas import Platform
from platforms.shared.config import ConfigError, load_settings

EXIT_OK = 0
EXIT_USAGE = 2

DEFAULT_OUTPUT = Path("artifacts/vertex-pipeline.yaml")


def vertex_step_environ(environ: dict[str, str]) -> dict[str, str]:
    """ステップへ渡す env を、ローカルで解決した Vertex 設定から組む。

    `MCML_VERTEX_*` が既に環境にあればそれが勝つ（`config.py` の解決順と同じ扱いで、
    明示指定を推測で上書きしない）。無い分だけ terraform outputs / config.yaml 由来の
    解決結果で埋める。
    """
    config = load_settings(environ=environ).for_platform(Platform.VERTEX)
    resolved = {
        "MCML_VERTEX_PROJECT": config.project,
        "MCML_VERTEX_REGION": config.region,
        "MCML_VERTEX_BUCKET": config.bucket,
        "MCML_VERTEX_TRAINING_IMAGE_URI": config.training_image_uri,
        "MCML_VERTEX_SERVICE_ACCOUNT": config.service_account or "",
    }
    merged = dict(environ)
    for name, value in resolved.items():
        if value and not merged.get(name):
            merged[name] = value
    return merged


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="compile_pipeline", description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--enable-caching",
        action="store_true",
        help="既定は無効。キャッシュ命中は『実行していない成功』を作り attempt と実態をずらす",
    )
    args = parser.parse_args(argv)

    try:
        environ = vertex_step_environ(dict(os.environ))
        image = environ["MCML_VERTEX_TRAINING_IMAGE_URI"]
    except (ConfigError, KeyError) as exc:
        print(f"設定が解決できない: {exc}", file=sys.stderr)
        return EXIT_USAGE

    from platforms.vertex.pipeline import compile_pipeline  # noqa: PLC0415 - kfp は任意 extra

    args.output.parent.mkdir(parents=True, exist_ok=True)
    compile_pipeline(image, environ, str(args.output), enable_caching=args.enable_caching)
    print(f"-- compiled: {args.output}（投入はしない。owner 承認の上で別途）")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
