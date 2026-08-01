"""オブジェクトストレージの差を吸収する（contracts.ports.ArtifactStore の実装）。

用途は2つ:

  1. **入力の配布**: `make dataset-export` が作った単一 Parquet を各基盤へ置く
     （docs/05_data_model.md「Parquet 化して各基盤のストレージへ配置」）
  2. **成果物の回収**: ジョブが書いた model.txt / run.json / ml_runs.jsonl を
     ローカルへ落とす（`make collect` の前段）

## 実装している基盤（Phase 順に足す・先回りしない）

    GCS   Phase 1（Vertex）      GcsArtifactStore
    S3    Phase 2（SageMaker）   S3ArtifactStore
    Blob  Phase 4（Azure ML）    BlobArtifactStore
    Volume  Databricks           adapter.upload_wheel / files API（既存）
    stage   Snowflake            adapter.upload_to_stage / session.file（既存）

Tier B の2つを別扱いにしているのは、**カタログ／スキーマの中にあるものを
オブジェクトストレージと同じ API で触れない**から。この非対称性自体が比較材料なので、
無理に1つの Protocol へ畳んで隠さない（contracts/ports.py「port を切る基準」）。

クライアントは注入する（実クラウドを叩かずに検証できるようにするため）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class ArtifactUriError(ValueError):
    """URI の形が想定と違う。バケット名の取り違えは黙って直さない。"""


def split_uri(uri: str, scheme: str) -> tuple[str, str]:
    """`<scheme>://bucket/prefix` を (bucket, prefix) に分ける。"""
    marker = f"{scheme}://"
    if not uri.startswith(marker):
        raise ArtifactUriError(f"{scheme}:// で始まらない URI: {uri}")
    bucket, _, prefix = uri[len(marker) :].partition("/")
    if not bucket:
        raise ArtifactUriError(f"bucket が空: {uri}")
    return bucket, prefix.strip("/")


def _files(local_dir: Path) -> list[Path]:
    """アップロード対象。サブディレクトリは辿らない（成果物は1階層）。"""
    if not local_dir.is_dir():
        raise ArtifactUriError(f"ディレクトリが無い: {local_dir}")
    found = sorted(p for p in local_dir.iterdir() if p.is_file())
    if not found:
        # 空を成功にすると「配ったつもり」で5基盤が別々に失敗する
        raise ArtifactUriError(f"アップロードするファイルが無い: {local_dir}")
    return found


class GcsArtifactStore:
    """Vertex AI 用（google-cloud-storage）。"""

    scheme = "gs"

    def __init__(self, client: Any) -> None:
        self._client = client

    def upload_dir(self, local_dir: Path, remote_uri: str) -> str:
        bucket_name, prefix = split_uri(remote_uri, self.scheme)
        bucket = self._client.bucket(bucket_name)
        for path in _files(local_dir):
            bucket.blob(f"{prefix}/{path.name}" if prefix else path.name).upload_from_filename(
                str(path)
            )
        return remote_uri

    def download_dir(self, remote_uri: str, local_dir: Path) -> Path:
        bucket_name, prefix = split_uri(remote_uri, self.scheme)
        local_dir.mkdir(parents=True, exist_ok=True)
        blobs = list(self._client.list_blobs(bucket_name, prefix=f"{prefix}/" if prefix else ""))
        if not blobs:
            raise ArtifactUriError(f"{remote_uri} にオブジェクトが無い")
        for blob in blobs:
            name = blob.name.rsplit("/", 1)[-1]
            if not name:  # ディレクトリマーカー
                continue
            blob.download_to_filename(str(local_dir / name))
        return local_dir


class BlobArtifactStore:
    """Azure ML 用（azure-storage-blob の BlobServiceClient）。

    URI は `https://<account>.blob.core.windows.net/<container>/<prefix>` ではなく
    **`abfs://<container>/<prefix>` 形式**に揃える（他2基盤と同じ「スキーム://入れ物/接頭辞」で
    扱えるようにするため。アカウントはクライアント側が持っている）。

    Azure だけ「アカウント > コンテナ > blob」の3階層で、GCS / S3 の
    「バケット > オブジェクト」より1段深い。その差はクライアント生成側へ寄せ、
    ここは他2基盤と同じ形に保つ。
    """

    scheme = "abfs"

    def __init__(self, client: Any) -> None:
        self._client = client

    def upload_dir(self, local_dir: Path, remote_uri: str) -> str:
        container, prefix = split_uri(remote_uri, self.scheme)
        for path in _files(local_dir):
            name = f"{prefix}/{path.name}" if prefix else path.name
            with path.open("rb") as f:
                self._client.get_blob_client(container=container, blob=name).upload_blob(
                    f, overwrite=True
                )
        return remote_uri

    def download_dir(self, remote_uri: str, local_dir: Path) -> Path:
        container, prefix = split_uri(remote_uri, self.scheme)
        local_dir.mkdir(parents=True, exist_ok=True)
        container_client = self._client.get_container_client(container)
        names = [
            n
            for n in container_client.list_blob_names(
                name_starts_with=f"{prefix}/" if prefix else ""
            )
            if not n.endswith("/")
        ]
        if not names:
            raise ArtifactUriError(f"{remote_uri} にオブジェクトが無い")
        for name in names:
            blob = self._client.get_blob_client(container=container, blob=name)
            (local_dir / name.rsplit("/", 1)[-1]).write_bytes(blob.download_blob().readall())
        return local_dir


class S3ArtifactStore:
    """SageMaker AI 用（boto3 の s3 クライアント）。"""

    scheme = "s3"

    def __init__(self, client: Any) -> None:
        self._client = client

    def upload_dir(self, local_dir: Path, remote_uri: str) -> str:
        bucket, prefix = split_uri(remote_uri, self.scheme)
        for path in _files(local_dir):
            key = f"{prefix}/{path.name}" if prefix else path.name
            self._client.upload_file(str(path), bucket, key)
        return remote_uri

    def download_dir(self, remote_uri: str, local_dir: Path) -> Path:
        bucket, prefix = split_uri(remote_uri, self.scheme)
        local_dir.mkdir(parents=True, exist_ok=True)
        listing = self._client.list_objects_v2(Bucket=bucket, Prefix=f"{prefix}/" if prefix else "")
        contents = listing.get("Contents") or []
        if not contents:
            raise ArtifactUriError(f"{remote_uri} にオブジェクトが無い")
        for entry in contents:
            key = entry["Key"]
            name = key.rsplit("/", 1)[-1]
            if not name:
                continue
            self._client.download_file(bucket, key, str(local_dir / name))
        return local_dir
