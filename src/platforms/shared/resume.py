"""Look up what a previous stage produced, so `resume` needs no arguments.

`run_phase.py <platform> resume` re-runs register → deploy → predict against an
already-trained model. Until now the caller had to supply the artifact URI by
hand, because the training success row is written **by the job** and the job
does not know that URI (see `contracts/tracking._merge_job_row_params`). With
the URI now persisted, resume can find it.

## Why a compatibility guard

Picking "the most recent successful train run" is only safe if that run used the
same training code as the checkout doing the resume. Otherwise resume would
register an artifact produced by different code and the comparison would be
quietly invalid.

The guard compares the **`src/core/ml` tree hash**, not the commit SHA. Running
five platforms in sequence produces five different commits (adapter and docs
land in between) while the training subtree stays byte-identical — that is
exactly what happened on 2026-08-01, and it is the invariant
`tests/comparison/test_code_revision_parity.py` pins.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.telemetry.schemas import Platform, Stage

REPO_ROOT = Path(__file__).resolve().parents[3]

# 学習ロジックの所在。ここが一致していれば「同じコードで作られた成果物」と言える。
TRAINING_SUBTREE = "src/core/ml"

# stage ごとに「次の段が要る値」を params のどのキーから取るか。
ARTIFACT_URI_KEY = "model_artifact_uri"
MODEL_REFERENCE_KEYS = (
    "model_resource_name",  # Vertex / Azure ML
    "model_version",  # Databricks / Snowflake
    "model_package_arn",  # SageMaker
)

_LATEST_SUCCESS_SQL = """
select run_id, code_revision, params
  from ml_runs
 where platform = %s
   and stage = %s
   and status = 'success'
   and params ? %s
 order by created_at desc
 limit 1
"""


class ResumeError(RuntimeError):
    """再開に使える値が見つからない・使ってはいけない。理由を文言に入れる。"""


@dataclass(frozen=True)
class ResumePoint:
    """前段が残した値と、それを作ったコードの素性。"""

    run_id: str
    code_revision: str
    value: str


def training_tree(revision: str = "HEAD") -> str | None:
    """`revision` 時点の `src/core/ml` の tree hash。解決できなければ None。

    None は「判定できない」であって「一致した」ではない。呼び出し側は
    確認できないまま先へ進まず、明示指定を求める。
    """
    result = subprocess.run(
        ["git", "rev-parse", f"{revision}:{TRAINING_SUBTREE}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _fetch_latest(platform: Platform, stage: Stage, key: str, connect: Any) -> ResumePoint | None:
    with connect() as conn:
        row = conn.execute(_LATEST_SUCCESS_SQL, (platform.value, stage.value, key)).fetchone()
    if row is None:
        return None
    run_id, revision, params = row
    value = (params or {}).get(key)
    if not value:
        # SQL の `params ? key` で絞っているので通常は起きない。
        # ここで KeyError にすると、SQL と取り出し側がずれた時に
        # 「再開元が無い」ではなく謎の例外として出る。
        return None
    return ResumePoint(run_id=str(run_id), code_revision=str(revision), value=str(value))


def _require_compatible(point: ResumePoint, stage: Stage) -> ResumePoint:
    """再開元が現在の checkout と同じ学習コードで作られたことを確かめる。"""
    current = training_tree("HEAD")
    recorded = training_tree(point.code_revision)
    if current is None or recorded is None:
        raise ResumeError(
            f"{stage.value} の再開元 {point.run_id} が使えるか判定できない"
            f"（{point.code_revision[:8]} を解決できない）。"
            f"明示指定で再開する: --artifact-uri / --model-version"
        )
    if current != recorded:
        raise ResumeError(
            f"{stage.value} の再開元 {point.run_id} は別の学習コードで作られている"
            f"（{TRAINING_SUBTREE}: 記録 {recorded[:8]} vs 現在 {current[:8]}）。"
            f"学習からやり直すか、承知のうえで明示指定する"
        )
    return point


def latest_artifact_uri(platform: Platform, *, connect: Any) -> ResumePoint:
    """直近の成功した学習が残した成果物 URI。"""
    point = _fetch_latest(platform, Stage.TRAIN, ARTIFACT_URI_KEY, connect)
    if point is None:
        raise ResumeError(
            f"{platform.value}: 再開できる学習成功 run が無い"
            f"（`{ARTIFACT_URI_KEY}` を持つ行が ml_runs に無い）。"
            f"`all` で train から通すか --artifact-uri を渡す"
        )
    return _require_compatible(point, Stage.TRAIN)


def latest_model_reference(platform: Platform, *, connect: Any) -> ResumePoint:
    """直近の成功した登録が残したモデル参照（deploy へ渡す値）。

    キー名が5基盤で違う（Vertex/Azure はリソース名、Tier B は版、SageMaker は ARN）。
    その差は `factory.deploy_reference` が持つ知識なので、ここでは
    **最初に見つかった1つ**を返し、意味付けは呼び出し側に委ねる。
    """
    for key in MODEL_REFERENCE_KEYS:
        point = _fetch_latest(platform, Stage.REGISTER, key, connect)
        if point is not None:
            return _require_compatible(point, Stage.REGISTER)
    raise ResumeError(
        f"{platform.value}: 再開できる登録成功 run が無い。"
        f"`resume` で register から通すか --model-version を渡す"
    )
