"""ArtifactStore（GCS / S3）の検証。**実クラウドを叩かない。**

入力 Parquet を各基盤へ配る経路と、成果物・JSONL を回収する経路。
ここが無いと Phase の先頭（データ配置）と末尾（fallback 回収）が手作業になる。

守る不変条件:
  - `gs://` / `s3://` の bucket と prefix を取り違えない
  - **空のアップロード・空のダウンロードを成功にしない**
    （配ったつもりで5基盤が別々に失敗するのが一番時間を溶かす）
  - ArtifactStore プロトコルを満たす（呼び出し側が基盤差を意識しない）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from platforms.shared.artifacts import (
    ArtifactUriError,
    BlobArtifactStore,
    GcsArtifactStore,
    S3ArtifactStore,
    split_uri,
)
from platforms.shared.contracts.ports import ArtifactStore

ARTIFACTS = {"model.txt": "tree\n", "run.json": '{"run_id": "x"}'}


@pytest.fixture
def local_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "model"
    directory.mkdir()
    for name, content in ARTIFACTS.items():
        (directory / name).write_text(content, encoding="utf-8")
    return directory


# --- GCS -----------------------------------------------------------------


class FakeBlob:
    def __init__(self, name: str, store: dict[str, str]) -> None:
        self.name = name
        self._store = store

    def upload_from_filename(self, path: str) -> None:
        self._store[self.name] = Path(path).read_text(encoding="utf-8")

    def download_to_filename(self, path: str) -> None:
        Path(path).write_text(self._store[self.name], encoding="utf-8")


class FakeBucket:
    def __init__(self, name: str, store: dict[str, str]) -> None:
        self.name = name
        self._store = store

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(name, self._store)


class FakeStorageClient:
    def __init__(self, store: dict[str, str] | None = None) -> None:
        self.store = store if store is not None else {}
        self.buckets: list[str] = []

    def bucket(self, name: str) -> FakeBucket:
        self.buckets.append(name)
        return FakeBucket(name, self.store)

    def list_blobs(self, bucket: str, prefix: str = "") -> list[FakeBlob]:
        self.buckets.append(bucket)
        return [FakeBlob(k, self.store) for k in sorted(self.store) if k.startswith(prefix)]


def test_gcs_upload_places_files_under_the_prefix(local_dir: Path) -> None:
    client = FakeStorageClient()

    GcsArtifactStore(client).upload_dir(local_dir, "gs://mcml-dev/data/california_housing")

    assert set(client.store) == {
        "data/california_housing/model.txt",
        "data/california_housing/run.json",
    }
    # バケットの解決は1回（ファイルごとに引き直さない）
    assert client.buckets == ["mcml-dev"]


def test_gcs_download_flattens_into_the_local_dir(tmp_path: Path) -> None:
    client = FakeStorageClient({"runs/abc/model/model.txt": "tree\n"})

    result = GcsArtifactStore(client).download_dir("gs://mcml-dev/runs/abc/model", tmp_path / "out")

    assert (result / "model.txt").read_text(encoding="utf-8") == "tree\n"


def test_gcs_download_of_nothing_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ArtifactUriError, match="オブジェクトが無い"):
        GcsArtifactStore(FakeStorageClient()).download_dir("gs://mcml-dev/runs/x", tmp_path)


# --- S3 -------------------------------------------------------------------


class FakeS3Client:
    def __init__(self, store: dict[str, str] | None = None) -> None:
        self.store = store if store is not None else {}
        self.requests: list[dict[str, Any]] = []

    def upload_file(self, filename: str, bucket: str, key: str) -> None:
        self.requests.append({"bucket": bucket, "key": key})
        self.store[key] = Path(filename).read_text(encoding="utf-8")

    def list_objects_v2(self, Bucket: str, Prefix: str = "") -> dict[str, Any]:  # noqa: N803
        keys = [k for k in sorted(self.store) if k.startswith(Prefix)]
        return {"Contents": [{"Key": k} for k in keys]} if keys else {}

    def download_file(self, bucket: str, key: str, filename: str) -> None:
        Path(filename).write_text(self.store[key], encoding="utf-8")


def test_s3_upload_uses_bucket_and_key(local_dir: Path) -> None:
    client = FakeS3Client()

    S3ArtifactStore(client).upload_dir(local_dir, "s3://mcml-dev/data/california_housing")

    assert {r["bucket"] for r in client.requests} == {"mcml-dev"}
    assert sorted(r["key"] for r in client.requests) == [
        "data/california_housing/model.txt",
        "data/california_housing/run.json",
    ]


def test_s3_download_writes_each_object(tmp_path: Path) -> None:
    client = FakeS3Client({"runs/abc/model/run.json": '{"run_id": "x"}'})

    result = S3ArtifactStore(client).download_dir("s3://mcml-dev/runs/abc/model", tmp_path / "out")

    assert (result / "run.json").read_text(encoding="utf-8") == '{"run_id": "x"}'


def test_s3_empty_listing_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ArtifactUriError, match="オブジェクトが無い"):
        S3ArtifactStore(FakeS3Client()).download_dir("s3://mcml-dev/runs/x", tmp_path)


# --- 共通契約 -------------------------------------------------------------


@pytest.mark.parametrize(
    ("store", "uri"),
    [
        (GcsArtifactStore(FakeStorageClient()), "s3://bucket/key"),
        (S3ArtifactStore(FakeS3Client()), "gs://bucket/key"),
    ],
    ids=["gcs-rejects-s3", "s3-rejects-gs"],
)
def test_scheme_mismatch_is_refused(store: Any, uri: str, local_dir: Path) -> None:
    """バケットの取り違えを黙って直さない。"""
    with pytest.raises(ArtifactUriError):
        store.upload_dir(local_dir, uri)


def test_uri_without_bucket_is_refused() -> None:
    with pytest.raises(ArtifactUriError, match="bucket が空"):
        split_uri("gs://", "gs")


def test_uri_without_prefix_uploads_to_the_root(local_dir: Path) -> None:
    client = FakeStorageClient()

    GcsArtifactStore(client).upload_dir(local_dir, "gs://mcml-dev")

    assert set(client.store) == {"model.txt", "run.json"}


def test_uploading_an_empty_directory_is_refused(tmp_path: Path) -> None:
    """「配ったつもり」を作らない。"""
    empty = tmp_path / "empty"
    empty.mkdir()

    with pytest.raises(ArtifactUriError, match="ファイルが無い"):
        S3ArtifactStore(FakeS3Client()).upload_dir(empty, "s3://mcml-dev/data")


@pytest.mark.parametrize(
    "store",
    [GcsArtifactStore(FakeStorageClient()), S3ArtifactStore(FakeS3Client())],
    ids=["gcs", "s3"],
)
def test_satisfies_the_artifact_store_protocol(store: Any) -> None:
    assert isinstance(store, ArtifactStore)


# --- Azure Blob -----------------------------------------------------------


class FakeBlobClient:
    def __init__(self, store: dict[str, str], name: str) -> None:
        self._store = store
        self._name = name

    def upload_blob(self, data: Any, overwrite: bool = False) -> None:
        self._store[self._name] = data.read().decode("utf-8")

    def download_blob(self) -> Any:
        content = self._store[self._name]
        return type("D", (), {"readall": staticmethod(lambda: content.encode("utf-8"))})()


class FakeContainerClient:
    def __init__(self, store: dict[str, str]) -> None:
        self._store = store

    def list_blob_names(self, name_starts_with: str = "") -> list[str]:
        return [n for n in sorted(self._store) if n.startswith(name_starts_with)]


class FakeBlobService:
    """azure-storage-blob の BlobServiceClient 代役。"""

    def __init__(self, store: dict[str, str] | None = None) -> None:
        self.store = store if store is not None else {}
        self.containers: list[str] = []

    def get_blob_client(self, container: str, blob: str) -> FakeBlobClient:
        self.containers.append(container)
        return FakeBlobClient(self.store, blob)

    def get_container_client(self, container: str) -> FakeContainerClient:
        self.containers.append(container)
        return FakeContainerClient(self.store)


def test_blob_upload_places_files_under_the_prefix(local_dir: Path) -> None:
    client = FakeBlobService()

    BlobArtifactStore(client).upload_dir(local_dir, "abfs://mcml/data/california_housing")

    assert set(client.store) == {
        "data/california_housing/model.txt",
        "data/california_housing/run.json",
    }
    assert set(client.containers) == {"mcml"}


def test_blob_download_flattens_into_the_local_dir(tmp_path: Path) -> None:
    client = FakeBlobService({"runs/abc/model/model.txt": "tree\n"})

    result = BlobArtifactStore(client).download_dir("abfs://mcml/runs/abc/model", tmp_path / "out")

    assert (result / "model.txt").read_text(encoding="utf-8") == "tree\n"


def test_blob_download_of_nothing_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ArtifactUriError, match="オブジェクトが無い"):
        BlobArtifactStore(FakeBlobService()).download_dir("abfs://mcml/runs/x", tmp_path)


def test_blob_store_satisfies_the_protocol_and_rejects_other_schemes(local_dir: Path) -> None:
    store = BlobArtifactStore(FakeBlobService())

    assert isinstance(store, ArtifactStore)
    with pytest.raises(ArtifactUriError):
        store.upload_dir(local_dir, "gs://bucket/key")


def test_all_three_tier_a_stores_share_the_same_interface(local_dir: Path) -> None:
    """Tier A 3基盤で呼び出し側が基盤差を意識しないこと（port の目的）。"""
    stores = [
        (GcsArtifactStore(FakeStorageClient()), "gs://b/p"),
        (S3ArtifactStore(FakeS3Client()), "s3://b/p"),
        (BlobArtifactStore(FakeBlobService()), "abfs://b/p"),
    ]
    for store, uri in stores:
        assert store.upload_dir(local_dir, uri) == uri
