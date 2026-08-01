"""ジョブ内テレメトリ（platforms/neon/job_record.py）の検証。

**このモジュールが比較軸「Neon 到達経路」の一次データを作る。**
adapter 側の記録は「オーケストレータから届いた」であって、
ジョブの egress を測っていない（docs/02_architecture.md「Neon 集約」）。

守る不変条件:
  - Neon へ届けば direct、届かなければ **JSONL へ退避して collected**
    （到達不能を握り潰さない = それ自体が結果）
  - JSONL は **成果物と同じディレクトリ**へ出す（`make collect` が回収できる場所）
  - 学習が成功したかどうか（exit code）が status / failure_class になる
  - **telemetry の失敗でジョブの終了コードを変えない**（06_error_policy）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from core.telemetry.schemas import FailureClass, MlRun, Platform, Stage, Status, WritePath
from core.telemetry.sinks import JSONL_FILENAME
from platforms.neon import job_record

MANIFEST = {
    "run_id": "11111111-1111-1111-1111-111111111111",
    "code_revision": "a" * 40,
    "metrics": {"rmse": 0.44, "mae": 0.3, "r2": 0.8},
    "params": {"num_leaves": 31},
}


class SpyWriter:
    """record_run を記録する sink。`explode` で到達不能を再現する。"""

    def __init__(self, write_path: WritePath, *, explode: bool = False) -> None:
        self._write_path = write_path
        self._explode = explode
        self.runs: list[MlRun] = []

    def record_run(self, run: MlRun) -> WritePath:
        if self._explode:
            raise ConnectionError("could not connect to server: Network is unreachable")
        self.runs.append(run)
        run.write_path = self._write_path
        return self._write_path


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "model"
    directory.mkdir()
    (directory / "run.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    return directory


# --- build_run: manifest から ml_runs 1行 --------------------------------


def test_manifest_supplies_identity_and_metrics(output_dir: Path) -> None:
    run = job_record.build_run(
        Platform.VERTEX, manifest=job_record.read_manifest(output_dir), attempt=2
    )

    assert run.run_id == MANIFEST["run_id"]
    assert run.code_revision == MANIFEST["code_revision"]
    assert run.metrics["rmse"] == 0.44
    assert run.params == {"num_leaves": 31}
    assert (run.stage, run.status, run.attempt) == (Stage.TRAIN, Status.SUCCESS, 2)
    assert run.failure_class is FailureClass.NONE


def test_tier_and_unit_follow_the_platform() -> None:
    """tier / unification_unit を手書きさせない（基盤ごとにばらつくと集計が壊れる）。"""
    tier_a = job_record.build_run(Platform.SAGEMAKER, manifest=MANIFEST)
    tier_b = job_record.build_run(Platform.DATABRICKS, manifest=MANIFEST)

    assert (tier_a.tier.value, tier_a.unification_unit.value) == ("A", "container")
    assert (tier_b.tier.value, tier_b.unification_unit.value) == ("B", "package")


@pytest.mark.parametrize(
    ("exit_code", "expected"),
    [(1, FailureClass.SDK), (2, FailureClass.DATA), (137, FailureClass.CONTAINER)],
)
def test_exit_code_maps_to_failure_class(exit_code: int, expected: FailureClass) -> None:
    """学習が最後まで行かなくても **必ず分類付きで記録する**（未分類の行を作らない）。"""
    run = job_record.build_run(
        Platform.VERTEX,
        manifest={},
        run_id="22222222-2222-2222-2222-222222222222",
        exit_code=exit_code,
        error_excerpt="core.ml.cli が exit した",
    )

    assert run.status is Status.FAILURE
    assert run.failure_class is expected
    assert run.error_excerpt
    assert run.code_revision  # manifest 不在でも環境から解決できること


def test_missing_run_id_is_an_error() -> None:
    """run_id が無い行は adapter 側の run と突合できない。黙って uuid を作らない。"""
    with pytest.raises(ValueError, match="run_id"):
        job_record.build_run(Platform.VERTEX, manifest={})


# --- record_job_run: 到達経路の確定 --------------------------------------


def test_neon_reachable_records_direct(output_dir: Path) -> None:
    neon = SpyWriter(WritePath.DIRECT)
    run = job_record.build_run(Platform.VERTEX, manifest=MANIFEST)

    result = job_record.record_job_run(run, output_dir, neon_sink=neon)

    assert result is WritePath.DIRECT
    assert [r.run_id for r in neon.runs] == [MANIFEST["run_id"]]
    assert not (output_dir / JSONL_FILENAME).exists()  # 退避は起きない


def test_unreachable_neon_falls_back_to_jsonl_beside_artifacts(output_dir: Path) -> None:
    """到達不能を握り潰さず collected へ落とす。**場所は成果物と同じ**（回収経路）。"""
    run = job_record.build_run(Platform.SNOWFLAKE, manifest=MANIFEST)

    result = job_record.record_job_run(
        run, output_dir, neon_sink=SpyWriter(WritePath.DIRECT, explode=True)
    )

    assert result is WritePath.COLLECTED
    record = json.loads((output_dir / JSONL_FILENAME).read_text(encoding="utf-8"))
    assert record["run_id"] == MANIFEST["run_id"]
    assert record["write_path"] == "collected"


def test_both_paths_failing_does_not_raise(output_dir: Path) -> None:
    """telemetry は非致命。記録できなくてもジョブを落とさない。"""
    run = job_record.build_run(Platform.VERTEX, manifest=MANIFEST)

    result = job_record.record_job_run(
        run,
        output_dir,
        neon_sink=SpyWriter(WritePath.DIRECT, explode=True),
        jsonl_sink=SpyWriter(WritePath.COLLECTED, explode=True),
    )

    assert result is None


# --- CLI: 終了コードは常に 0 ---------------------------------------------


def test_cli_records_via_fallback_and_exits_zero(output_dir: Path) -> None:
    """既定経路（Neon 未設定のローカル）でも JSONL に落ちて 0 で終わること。"""
    code = job_record.main(["--platform", "vertex", "--output", str(output_dir), "--attempt", "3"])

    assert code == 0
    record = json.loads((output_dir / JSONL_FILENAME).read_text(encoding="utf-8"))
    assert record["attempt"] == 3
    assert record["platform"] == "vertex"


def test_cli_survives_unusable_input(tmp_path: Path) -> None:
    """run.json も --run-id も無い（= 記録不能）でもジョブの結果を変えない。"""
    assert job_record.main(["--platform", "vertex", "--output", str(tmp_path)]) == 0


def test_broken_manifest_is_treated_as_absent(tmp_path: Path) -> None:
    """途中で落ちて run.json が壊れている場合も、失敗行は残せること。"""
    (tmp_path / "run.json").write_text("{ truncated", encoding="utf-8")

    assert job_record.read_manifest(tmp_path) == {}

    code = job_record.main(
        [
            "--platform",
            "sagemaker",
            "--output",
            str(tmp_path),
            "--run-id",
            "33333333-3333-3333-3333-333333333333",
            "--exit-code",
            "1",
        ]
    )

    assert code == 0
    record = json.loads((tmp_path / JSONL_FILENAME).read_text(encoding="utf-8"))
    assert record["status"] == "failure"
    assert record["failure_class"] == "sdk"


# --- telemetry_env: ジョブへ渡す接続情報 ----------------------------------


def test_telemetry_env_passes_pooled_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    from platforms.shared.contracts.tracking import NEON_POOLED_URI_ENV, telemetry_env

    monkeypatch.setenv(NEON_POOLED_URI_ENV, "postgresql://example.invalid/db")

    assert telemetry_env() == {NEON_POOLED_URI_ENV: "postgresql://example.invalid/db"}


def test_telemetry_env_is_empty_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """未設定でも例外にしない（ジョブは JSONL fallback に落ちる = 比較データ）。"""
    from platforms.shared.contracts.tracking import NEON_POOLED_URI_ENV, telemetry_env

    monkeypatch.delenv(NEON_POOLED_URI_ENV, raising=False)

    assert telemetry_env() == {}


def test_code_revision_is_never_forwarded_from_the_orchestrator() -> None:
    """CODE_REVISION を渡すと「実際に動いたコード」と記録がずれる。

    コンテナはビルド時に焼き込んだ値を持つ。ここで上書きすると
    比較成立の唯一の担保（同一SHA）が意味を失う。
    """
    from platforms.shared.contracts import tracking

    source = Path(tracking.__file__).read_text(encoding="utf-8")
    body = source.split("def telemetry_env()")[1].split("\ndef ")[0]
    # docstring の説明としては出てくるが、**dict に載せていない**ことを見る
    assert "CODE_REVISION" not in body.split('"""')[2]


def test_job_record_does_not_import_cloud_sdks() -> None:
    """学習コンテナに入るモジュールなので、依存はコンテナに在るものだけ。"""
    source = Path(job_record.__file__).read_text(encoding="utf-8")
    for forbidden in ("google.cloud", "boto3", "azure.ai", "databricks.sdk", "snowflake."):
        assert f"import {forbidden}" not in source


def test_recorded_metrics_survive_the_jsonl_roundtrip(output_dir: Path) -> None:
    """collect が読み戻せる形であること（sinks.record_to_run と対）。"""
    from core.telemetry.sinks import record_to_run

    run = job_record.build_run(Platform.VERTEX, manifest=MANIFEST)
    job_record.record_job_run(run, output_dir, neon_sink=SpyWriter(WritePath.DIRECT, explode=True))

    line = (output_dir / JSONL_FILENAME).read_text(encoding="utf-8").strip()
    restored: Any = record_to_run(json.loads(line))

    assert restored.metrics["rmse"] == 0.44
    assert restored.write_path is WritePath.COLLECTED
