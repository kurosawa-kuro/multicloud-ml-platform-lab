"""Vertex の `AIP_STORAGE_URI`（gs://）をローカルへ取得する起動シム。

`src/core/app/serving/predictor.py` が契約として書いている
「起動シムが GCS からローカルへ取得し MODEL_DIR を設定する」の実装。

**stdlib だけで書く。** 推論イメージは3基盤共通なので、ここに
google-cloud-storage を入れると SageMaker / Azure ML のコンテナにも
GCP SDK が乗る（docs/02_architecture.md「1イメージで3契約」）。
GCS の JSON API は素の HTTP なので urllib で足りる。

認証はメタデータサーバのアクセストークン（Vertex の予測コンテナは
サービスアカウントを持つ）。鍵ファイルは扱わない。

    python fetch_gcs_model.py --uri gs://bucket/runs/<id>/model --dest /tmp/model

`src/` に置かないのは、これが**イメージの起動手順**であって
5基盤共通コード（core）でも adapter（platforms）でもないため。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
)
STORAGE_API = "https://storage.googleapis.com/storage/v1/b"

# HTTP の差し替え口（テストで実 GCS を叩かないため）。
# 戻り値は bytes。
Opener = Callable[[str, dict[str, str]], bytes]


class FetchError(RuntimeError):
    """モデルの取得に失敗した。**推論コンテナはここで止めるべき**。

    取得できないまま uvicorn を上げると、ヘルスチェックは通るのに
    推論だけ失敗する状態になり、原因究明が遅れる。
    """


def default_opener(url: str, headers: dict[str, str]) -> bytes:
    # 対象はメタデータサーバ（http・リンクローカル）と GCS API（https）の2つだけ。
    # URL は本モジュール内の定数からしか組み立てない（S310 の懸念は任意 URL の場合）。
    request = urllib.request.Request(url, headers=headers)  # noqa: S310
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        return bytes(response.read())


def access_token(opener: Opener = default_opener) -> str:
    """メタデータサーバからアクセストークンを取る。"""
    payload = json.loads(opener(METADATA_TOKEN_URL, {"Metadata-Flavor": "Google"}))
    token = payload.get("access_token")
    if not token:
        raise FetchError("メタデータサーバがアクセストークンを返さない")
    return str(token)


def parse_gs_uri(uri: str) -> tuple[str, str]:
    """gs://bucket/prefix を (bucket, prefix) に分ける。"""
    if not uri.startswith("gs://"):
        raise FetchError(f"gs:// ではない URI: {uri}")
    bucket, _, prefix = uri[len("gs://") :].partition("/")
    if not bucket:
        raise FetchError(f"bucket が空: {uri}")
    return bucket, prefix.strip("/")


def list_objects(
    bucket: str, prefix: str, *, token: str, opener: Opener = default_opener
) -> list[str]:
    """prefix 配下のオブジェクト名を返す（1ページ目だけ見れば足りる件数）。"""
    query = urllib.parse.urlencode({"prefix": f"{prefix}/" if prefix else ""})
    payload: dict[str, Any] = json.loads(
        opener(f"{STORAGE_API}/{bucket}/o?{query}", {"Authorization": f"Bearer {token}"})
    )
    return [item["name"] for item in payload.get("items", []) if not item["name"].endswith("/")]


def fetch(
    uri: str,
    dest: Path,
    *,
    opener: Opener = default_opener,
    token: str | None = None,
) -> list[Path]:
    """`uri` 配下のファイルを `dest` へ平らに落とす。

    サブディレクトリは作らない。predictor が読むのは `dest/model.txt` であり、
    Vertex の成果物は1階層なので、ここで階層を再現する必要が無い。
    """
    bucket, prefix = parse_gs_uri(uri)
    resolved = token if token is not None else access_token(opener)
    names = list_objects(bucket, prefix, token=resolved, opener=opener)
    if not names:
        raise FetchError(f"{uri} にオブジェクトが無い（学習成果物の場所を確認）")

    dest.mkdir(parents=True, exist_ok=True)
    written = []
    for name in names:
        encoded = urllib.parse.quote(name, safe="")
        blob = opener(
            f"{STORAGE_API}/{bucket}/o/{encoded}?alt=media",
            {"Authorization": f"Bearer {resolved}"},
        )
        path = dest / Path(name).name
        path.write_bytes(blob)
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fetch_gcs_model")
    parser.add_argument("--uri", required=True, help="AIP_STORAGE_URI（gs://...）")
    parser.add_argument("--dest", required=True, type=Path, help="展開先（MODEL_DIR にする）")
    args = parser.parse_args(argv)

    try:
        written = fetch(args.uri, args.dest)
    except Exception as exc:  # noqa: BLE001 - 取得できないなら起動させない
        print(f"fetch_gcs_model: {exc}", file=sys.stderr)
        return 1

    print(f"fetch_gcs_model: {len(written)} files -> {args.dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
