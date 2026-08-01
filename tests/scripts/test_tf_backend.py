"""Neon backend の接続文字列組み立て（scripts/tf_backend.py）。

Tier B は自前で tfstate を置けない（UC Volume も Snowflake stage も Terraform
backend ではない）。以前は GCP のバケットに相乗りしており、**GCP を畳むと
Tier B 2基盤の state を失う**状態だった。Neon は比較対象の5基盤に含まれないので
中立の置き場になる。

ここで固定するのは、手打ちでは再現できない Neon 固有の2点:

  1. **direct endpoint を使う**（pooled では pg backend の advisory lock が動かない）
  2. **`options=endpoint=<id>` を付ける**（lib/pq が SNI 非対応。無いと init が落ちる）
"""

from __future__ import annotations

import urllib.parse as urlparse

import pytest
from tests.conftest import load_script

tf_backend = load_script("tf_backend")

DIRECT = (
    "postgresql://u:p@ep-example-endpoint-a1b2c3.c-3.ap-southeast-1.aws.neon.tech/db?sslmode=require"
)


def _query(flag: str) -> dict[str, str]:
    conn = flag.split("conn_str=", 1)[1]
    return dict(urlparse.parse_qsl(urlparse.urlparse(conn).query))


def test_endpoint_option_is_added() -> None:
    """SNI 非対応の回避。無いと `Endpoint ID is not specified` で init が落ちる。"""
    query = _query(tf_backend.backend_flags("dbx-dev", {tf_backend.DIRECT_URI_ENV: DIRECT})[0])

    assert query["options"] == "endpoint=ep-example-endpoint-a1b2c3"


def test_existing_query_parameters_are_preserved() -> None:
    """`sslmode` 等を落とさない（落とすと接続そのものが変わる）。"""
    query = _query(tf_backend.backend_flags("sf-dev", {tf_backend.DIRECT_URI_ENV: DIRECT})[0])

    assert query["sslmode"] == "require"


def test_schema_is_per_environment() -> None:
    """環境ごとに schema を分ける（1つの states テーブルに混ぜない）。"""
    flags = tf_backend.backend_flags("dbx-dev", {tf_backend.DIRECT_URI_ENV: DIRECT})

    assert "-backend-config=schema_name=tfstate_dbx_dev" in flags


def test_pooled_uri_is_not_used() -> None:
    """direct を要求する。pooled を渡す口を作らない（advisory lock が動かない）。"""
    assert tf_backend.DIRECT_URI_ENV.endswith("DIRECT_URI")


def test_missing_uri_says_to_use_doppler() -> None:
    with pytest.raises(tf_backend.BackendError, match="doppler run"):
        tf_backend.backend_flags("dbx-dev", {})


def test_non_neon_environment_is_rejected() -> None:
    """Tier A は自分のクラウドに置く。ここへ紛れ込ませない。"""
    with pytest.raises(tf_backend.BackendError, match="Neon backend ではない"):
        tf_backend.backend_flags("gcp-dev", {tf_backend.DIRECT_URI_ENV: DIRECT})


def test_tier_b_backends_are_pg_not_gcs() -> None:
    """backend.tf が GCS 相乗りへ戻っていないこと（修正09 の回帰防止）。"""
    from tests.conftest import REPO_ROOT

    for env in tf_backend.NEON_BACKED:
        source = (REPO_ROOT / "infra" / "environments" / env / "backend.tf").read_text(
            encoding="utf-8"
        )
        assert 'backend "pg"' in source, f"{env} が pg backend でない"
        assert "example-gcp-project-tfstate" not in source, f"{env} が GCP バケットに戻っている"
