"""推論イメージの起動シム（docker/serving/fetch_gcs_model.py）の検証。

**実 GCS を叩かない**（HTTP を注入する）。ここが壊れると Vertex の
1件推論（フェーズ完了条件⑤）が通らず、比較レポートの1列が埋まらない。

守る不変条件:
  - gs:// URI を bucket / prefix に正しく割る
  - prefix 配下を平らに落とす（predictor が読むのは MODEL_DIR/model.txt）
  - **空の取得を成功にしない**（モデル無しで uvicorn を上げるとヘルスチェックだけ
    通って推論が失敗し、原因究明が遅れる）
  - 認証はメタデータサーバのトークン（鍵ファイルを扱わない）
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
from tests.conftest import REPO_ROOT

REPO_ROOT = REPO_ROOT


def _load() -> Any:
    path = REPO_ROOT / "docker" / "serving" / "fetch_gcs_model.py"
    spec = importlib.util.spec_from_file_location("fetch_gcs_model", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fetch_gcs_model = _load()


class FakeGcs:
    """メタデータサーバ + GCS JSON API の代役。"""

    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.requests: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, headers: dict[str, str]) -> bytes:
        self.requests.append((url, headers))
        if "metadata.google.internal" in url:
            return json.dumps({"access_token": "ya29.fake"}).encode()
        if "alt=media" in url:
            import urllib.parse

            name = urllib.parse.unquote(url.split("/o/")[1].split("?")[0])
            return self.objects[name]
        return json.dumps({"items": [{"name": n} for n in self.objects]}).encode()


ARTIFACTS = {
    "runs/abc/model/model.txt": b"tree\nversion=v4\n",
    "runs/abc/model/metrics.json": b'{"rmse": 0.44}',
    "runs/abc/model/run.json": b'{"run_id": "abc"}',
}


def test_uri_is_split_into_bucket_and_prefix() -> None:
    assert fetch_gcs_model.parse_gs_uri("gs://mcml-dev/runs/abc/model") == (
        "mcml-dev",
        "runs/abc/model",
    )


@pytest.mark.parametrize("uri", ["/local/path", "s3://bucket/key", "gs://"])
def test_non_gcs_uri_is_rejected(uri: str) -> None:
    with pytest.raises(fetch_gcs_model.FetchError):
        fetch_gcs_model.parse_gs_uri(uri)


def test_artifacts_land_flat_in_the_model_dir(tmp_path: Path) -> None:
    """predictor は MODEL_DIR/model.txt を読む。階層を再現しない。"""
    gcs = FakeGcs(ARTIFACTS)

    written = fetch_gcs_model.fetch("gs://mcml-dev/runs/abc/model", tmp_path, opener=gcs)

    assert {p.name for p in written} == {"model.txt", "metrics.json", "run.json"}
    assert (tmp_path / "model.txt").read_bytes() == ARTIFACTS["runs/abc/model/model.txt"]


def test_empty_prefix_is_an_error(tmp_path: Path) -> None:
    """モデルが無いまま起動させない。"""
    with pytest.raises(fetch_gcs_model.FetchError, match="オブジェクトが無い"):
        fetch_gcs_model.fetch("gs://mcml-dev/runs/missing", tmp_path, opener=FakeGcs({}))


def test_token_comes_from_the_metadata_server(tmp_path: Path) -> None:
    """鍵ファイルを配らない（サービスアカウントの権限だけで動く）。"""
    gcs = FakeGcs(ARTIFACTS)

    fetch_gcs_model.fetch("gs://mcml-dev/runs/abc/model", tmp_path, opener=gcs)

    first_url, first_headers = gcs.requests[0]
    assert "metadata.google.internal" in first_url
    assert first_headers == {"Metadata-Flavor": "Google"}
    assert all(h.get("Authorization") == "Bearer ya29.fake" for _, h in gcs.requests[1:])


def test_object_names_are_url_encoded(tmp_path: Path) -> None:
    """`/` を含むオブジェクト名は encode しないと 404 になる。"""
    gcs = FakeGcs(ARTIFACTS)

    fetch_gcs_model.fetch("gs://mcml-dev/runs/abc/model", tmp_path, opener=gcs)

    media = [url for url, _ in gcs.requests if "alt=media" in url]
    assert any("runs%2Fabc%2Fmodel%2Fmodel.txt" in url for url in media)


def test_cli_reports_failure_with_exit_code_one(tmp_path: Path) -> None:
    """取得できなければ 1（entrypoint が set -e で起動を止める）。"""
    assert fetch_gcs_model.main(["--uri", "not-a-uri", "--dest", str(tmp_path)]) == 1


def test_module_uses_only_stdlib() -> None:
    """推論イメージは3基盤共通。GCP SDK を入れない（1イメージ3契約の前提）。"""
    source = (REPO_ROOT / "docker" / "serving" / "fetch_gcs_model.py").read_text(encoding="utf-8")

    for forbidden in ("google.cloud", "google.auth", "requests", "boto3"):
        assert forbidden not in source
