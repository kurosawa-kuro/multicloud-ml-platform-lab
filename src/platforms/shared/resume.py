"""Stage 間の受け渡し（handoff）の正本 —— 書き側と読み側を1箇所に持つ。

**この機能は記録の契約（RunSink）ではなく、driver（run_phase）の機能である。**
学習の成功行はジョブが書く規約のため、adapter しか知らない値（model_artifact_uri 等）を
後からジョブ行へ足さないと、stage を跨いだ再開ができない。

系統差はここで**吸収せず、明示する**（02_architecture「設計の導出順序」手順3）:

    direct 系統（Tier A）  : ジョブ行が既に Neon にある → persist_handoff が merge する
    collected 系統（Tier B）: 行は `make collect` 後に現れる → merge する相手が無い。
                              受け渡し値は config から決定的に導出するか明示指定する

かつてこの書き側は RunSink 契約の `merge_run_params` として全 sink に強制され、
JSONL には no-op が生えていた。**片系統にしか意味の無い操作を契約で均すと、
非対称が silent no-op に化けて事故になる**（2026-08-02 に decorator がこれを落として
再開が壊れた）。非対称は隠さず、この1箇所で扱う。

Look up what a previous stage produced, so `resume` needs no arguments.

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

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from core.telemetry.schemas import MlRun, Platform, Stage

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
    try:
        result = subprocess.run(
            ["git", "rev-parse", f"{revision}:{TRAINING_SUBTREE}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, NotADirectoryError):
        # **git が無い環境**（コンテナ内の driver。orchestrator イメージには .git も
        # git バイナリも無い）。ここで例外を上げると resume が到達不能になる
        # ——2026-08-02 に Vertex パイプラインの register ステップが
        # `FileNotFoundError: 'git'` で落ちて実測。
        #
        # **None を返すのは「判定できない」の意味**であり「一致した」ではない。
        # 呼び出し側（_require_compatible）は None を互換 OK と扱わず、
        # 明示指定を求めて止まる ＝ 安全側に倒れる設計は保たれる。
        return None
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
    """再開元が現在の checkout と同じ学習コードで作られたことを確かめる。

    ## git が無い環境（コンテナ内の driver）での判定

    tree hash 比較は git を要る。器（orchestrator イメージ）には git も .git も無い
    ——**しかしビルド時に `CODE_REVISION` が焼き込まれている**。焼き込んだ値と
    記録された `code_revision` が**同一なら、同じコードであることは自明**
    （tree hash を引くまでもない）。ここだけ先に判定する。

    緩めているのではない: 一致しない場合は従来どおり tree hash 判定へ進み、
    それが不能なら **止まる**（判定できないまま先へ進まない設計は不変）。
    2026-08-02 に Vertex パイプラインの register ステップで実測した経路。
    """
    from core.ml.config.revision import code_revision  # noqa: PLC0415 - 循環回避

    try:
        if code_revision() == point.code_revision:
            return point
    except Exception:  # noqa: BLE001 - 解決できないなら tree hash 判定へ落とす
        pass

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


# --- 書き側（persist）------------------------------------------------------


@runtime_checkable
class SupportsHandoffMerge(Protocol):
    """ジョブ行へ params を後付けできる記録先（実体は NeonRunSink だけ）。

    RunSink 契約には**入れない**（片実装。core/telemetry/tracking.py の docstring）。
    isinstance 判定をこの1箇所に閉じ込め、満たさない sink は「collected 系統」として
    **明示的にスキップする**。かつての getattr 拾いとの違いは、スキップが仕様として
    ログに出ること（silent no-op にしない）。
    """

    def merge_run_params(self, run_id: str, params: dict[str, Any]) -> int: ...


def persist_handoff(run: MlRun, sink: Any) -> int | None:
    """train 成功 run の受け渡し値をジョブ行へ足す。**driver が train の直後に呼ぶ。**

    戻り値: merge した行数 / None = この sink では merge できない（collected 系統）。
    telemetry は非致命（docs/06_error_policy.md）。失敗しても driver の結果を変えない。

    0 行は異常ではない（direct 系統でもジョブ行の INSERT が僅かに遅れうる）。
    None と 0 を区別して返すのは、「できない」と「相手がまだ居ない」が別だから。
    """
    if not run.params:
        return 0
    if not isinstance(sink, SupportsHandoffMerge):
        logging.getLogger("platforms.resume").info(
            "handoff は永続化しない（collected 系統: %s）。再開は決定的パスか明示指定で",
            type(sink).__name__,
        )
        return None
    try:
        return sink.merge_run_params(run.run_id, dict(run.params))
    except Exception:  # noqa: BLE001 - 記録の失敗で学習結果を変えない
        logging.getLogger("platforms.resume").warning(
            "run %s の handoff 永続化に失敗", run.run_id, exc_info=True
        )
        return 0
