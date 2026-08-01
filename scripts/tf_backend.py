"""Emit `terraform init` backend flags for the environments that use Neon.

Tier B (Databricks / Snowflake) cannot host its own tfstate: neither a UC Volume
nor a Snowflake stage is a Terraform backend, and neither platform exposes an
object store. They used to ride on the GCP state bucket, which meant **tearing
down GCP would take two other platforms' state with it**. Neon is the neutral
store — it is the measurement destination, not one of the five subjects.

Two Neon-specific details make this impossible to type from memory, which is why
it lives in a script rather than in the runbooks:

1. **direct endpoint, not pooled.** The `pg` backend locks state with a
   session-level advisory lock, which transaction pooling does not support
   (`docs/02_architecture.md`).
2. **`options=endpoint=<id>` in the connection string.** The backend's `lib/pq`
   has no SNI support, so a plain Neon URI fails init with
   `Endpoint ID is not specified` (observed 2026-08-01).

    doppler run -- python scripts/tf_backend.py dbx-dev \
      | xargs terraform -chdir=infra/environments/dbx-dev init

exit code 規約（.claude/rules/scripts.md）: 0=成功 / 2=引数・設定不備
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.parse as urlparse

EXIT_OK = 0
EXIT_USAGE = 2

# Neon を backend に使う環境（Tier B のみ。他3基盤は自分のクラウドに置く）。
NEON_BACKED = {"dbx-dev": "tfstate_dbx_dev", "sf-dev": "tfstate_sf_dev"}

DIRECT_URI_ENV = "NEON_MULTICLOUD_DIRECT_URI"


class BackendError(RuntimeError):
    """接続文字列を組み立てられない。何を設定すべきかを文言に入れる。"""


def with_endpoint_option(uri: str) -> str:
    """Neon の URI へ `options=endpoint=<id>` を足す（SNI 非対応の回避）。

    endpoint id はホスト名の先頭ラベル
    （`ep-example-endpoint-xxxx.c-3.<region>.aws.neon.tech` → `ep-example-endpoint-xxxx`）。
    既存のクエリ（`sslmode` 等）は保つ。
    """
    parsed = urlparse.urlparse(uri)
    if not parsed.hostname:
        raise BackendError(f"{DIRECT_URI_ENV} からホスト名を読めない")
    query = dict(urlparse.parse_qsl(parsed.query))
    query["options"] = f"endpoint={parsed.hostname.split('.')[0]}"
    return urlparse.urlunparse(parsed._replace(query=urlparse.urlencode(query)))


def backend_flags(env: str, environ: dict[str, str]) -> list[str]:
    """`terraform init` に渡す `-backend-config` フラグ。"""
    if env not in NEON_BACKED:
        raise BackendError(
            f"{env} は Neon backend ではない（対象: {', '.join(sorted(NEON_BACKED))}）。"
            f"他の環境は各クラウドのバケットを使う"
        )
    uri = environ.get(DIRECT_URI_ENV, "")
    if not uri:
        raise BackendError(
            f"{DIRECT_URI_ENV} が無い。`doppler run --` 経由で実行する"
            f"（**pooled ではなく direct**。pg backend の advisory lock が transaction "
            f"pooling で動かないため）"
        )
    return [
        f"-backend-config=conn_str={with_endpoint_option(uri)}",
        f"-backend-config=schema_name={NEON_BACKED[env]}",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tf_backend")
    parser.add_argument("env", help=f"terraform 環境名（{', '.join(sorted(NEON_BACKED))}）")
    args = parser.parse_args(argv)

    try:
        print(" ".join(backend_flags(args.env, dict(os.environ))))
    except BackendError as exc:
        print(f"backend 設定エラー: {exc}", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    raise SystemExit(main())
