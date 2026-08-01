"""配布 CLI（scripts/distribute.py）の契約。

`ArtifactStore` は Phase 1 から実装済みだったが**呼び出し口が無く**、
配布は各 runbook の手打ちコマンドに散っていた（修正05）。ここで固定するのは:

  1. Tier B を配布対象に含めない（adapter が wheel / zip を運ぶ。口を二重に作らない）
  2. 配る物が無ければ **upload の前に**落ちる（空アップロードで「配ったつもり」を作らない）
  3. 1基盤の失敗で残りを止めない（--all の途中失敗が全部を巻き込まない）

実クラウドは叩かない。store とクライアントは注入する。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.conftest import REPO_ROOT, load_script

from core.telemetry.schemas import Platform
from platforms.shared.config import ConfigError

distribute = load_script("distribute")


def test_tier_b_is_not_a_distribution_target() -> None:
    """Databricks / Snowflake は adapter が運ぶ。ここに口を作らない。"""
    assert Platform.DATABRICKS not in distribute.DISTRIBUTABLE
    assert Platform.SNOWFLAKE not in distribute.DISTRIBUTABLE
    assert set(distribute.DISTRIBUTABLE) == {Platform.VERTEX, Platform.SAGEMAKER, Platform.AZUREML}


def test_missing_dataset_fails_before_upload(tmp_path: Path) -> None:
    """配る物が無いのに store を触らないこと（空アップロードを成功にしない）。"""

    class ExplodingStore:
        def upload_dir(self, *_: object) -> str:  # pragma: no cover - 呼ばれたら失敗
            raise AssertionError("配る物が無いのに upload された")

    def build_target(_platform: object, _settings: object) -> tuple[object, str]:
        return ExplodingStore(), "gs://bucket/prefix"

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(distribute, "build_target", build_target)
        with pytest.raises(ConfigError, match="make dataset-export"):
            distribute.distribute(Platform.VERTEX, tmp_path, settings=None)


def test_uploads_the_dataset_directory(tmp_path: Path) -> None:
    (tmp_path / "california_housing.parquet").write_bytes(b"x")
    calls: list[tuple[Path, str]] = []

    class RecordingStore:
        def upload_dir(self, local_dir: Path, remote_uri: str) -> str:
            calls.append((local_dir, remote_uri))
            return remote_uri

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            distribute,
            "build_target",
            lambda _p, _s: (RecordingStore(), "gs://bucket/data/california_housing"),
        )
        uri = distribute.distribute(Platform.VERTEX, tmp_path, settings=None)

    assert uri == "gs://bucket/data/california_housing"
    assert calls == [(tmp_path, "gs://bucket/data/california_housing")]


def test_one_platform_failure_does_not_stop_the_rest(tmp_path: Path, capsys) -> None:
    (tmp_path / "california_housing.parquet").write_bytes(b"x")
    attempted: list[Platform] = []

    def flaky(platform: Platform, _dir: Path, _settings: object) -> str:
        attempted.append(platform)
        if platform is Platform.SAGEMAKER:
            raise RuntimeError("ECR ログイン失敗")
        return "ok://"

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(distribute, "distribute", flaky)
        patch.setattr(distribute, "load_settings", lambda: None)
        code = distribute.main(["--all", "--dataset-dir", str(tmp_path)])

    assert code == distribute.EXIT_FAILED
    assert attempted == list(distribute.DISTRIBUTABLE), "失敗した基盤で打ち切られている"


def test_platform_and_all_are_mutually_exclusive(tmp_path: Path) -> None:
    assert distribute.main(["vertex", "--all"]) == distribute.EXIT_USAGE
    assert distribute.main([]) == distribute.EXIT_USAGE


# --- Makefile の入口（手打ち手順を残さない）--------------------------------


def test_make_exposes_distribution_targets() -> None:
    """配布と push が make の入口を持つこと。

    無いと runbook の手打ちコマンドへ戻る（修正05 が戻る）。
    """
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    for target in ("distribute:", "docker-push:"):
        assert target in makefile, f"Makefile に {target} が無い"


def test_quality_gate_covers_scripts() -> None:
    """`make fmt` / `make lint` が scripts/ を見ていること。

    実行系の本体（run_phase / run_terraform / distribute / check_residual）は
    scripts/ に居る。対象から外すと「動くコードの一部だけ無検査」になり、
    しかも外れたことに誰も気付かない（2026-08-01 まで実際に外れていた）。
    """
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    paths = next(
        line.split(":=", 1)[1] for line in makefile.splitlines() if line.startswith("LINT_PATHS")
    )

    assert {"src", "tests", "scripts"} <= set(paths.split()), (
        f"品質ゲートの対象が足りない: {paths.strip()}"
    )


def test_sagemaker_manifest_workaround_lives_in_the_script() -> None:
    """SageMaker の OCI index 回避が runbook ではなくスクリプトにあること。

    `CreateModel` は OCI image index を拒否する。**Training は受理する**ので
    ②が通っても⑤で初めて落ちる。手順書にしか無いと必ず踏む。
    """
    script = (REPO_ROOT / "scripts" / "push_images.sh").read_text(encoding="utf-8")

    assert "oci-mediatypes=false" in script
    # push しただけで満足せず、形式を検証していること
    assert "imageManifestMediaType" in script
