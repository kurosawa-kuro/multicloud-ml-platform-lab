"""試行を必ず1行の ml_runs として残す層。

**このモジュールが本プロジェクトの主目的を支える。**
[docs/02_architecture.md] が「核心フィールドは failure_class と attempt。
『最小権限で学習ジョブが通るまでに何回直したか』を出す permission friction
クエリが本命」と定めている。成功した run だけ記録するとこの情報が消えるため、
**例外が出ても必ず MlRun を1行出してから再送出する**。

使い方（platforms/*/adapter.py 側）:

    with track(Platform.VERTEX, Stage.TRAIN, recorder=recorder) as run:
        result = submit_and_wait(...)
        run.metrics = result.metrics

失敗時は自動で status=failure / failure_class を埋めて記録し、例外はそのまま
呼び出し元へ抜ける（握り潰さない）。attempt は同一 (platform, stage) の
過去 run 数 + 1 を recorder が解決する。
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from core.ml.config.revision import code_revision
from core.telemetry.schemas import (
    FailureClass,
    MlRun,
    Platform,
    Stage,
    Status,
    Tier,
    UnificationUnit,
    WritePath,
)

# Tier / 統一単位は基盤で決まるので、ここで一元的に持つ。
# adapter 側で個別に書くと値がばらつき、tier 別の集計が壊れる。
PLATFORM_TIER: dict[Platform, tuple[Tier, UnificationUnit]] = {
    Platform.VERTEX: (Tier.A, UnificationUnit.CONTAINER),
    Platform.SAGEMAKER: (Tier.A, UnificationUnit.CONTAINER),
    Platform.AZUREML: (Tier.A, UnificationUnit.CONTAINER),
    Platform.DATABRICKS: (Tier.B, UnificationUnit.PACKAGE),
    Platform.SNOWFLAKE: (Tier.B, UnificationUnit.PACKAGE),
}

ERROR_EXCERPT_MAX_CHARS = 2000

_logger = logging.getLogger("core.telemetry")

# 例外メッセージ → failure_class の推定に使う語。
# 推定を外すこと自体は許容するが、**未分類のまま記録しない**ことが重要。
# 分類できなければ SDK 扱いにし、後から手で直せるよう error_excerpt を残す。
_FAILURE_HINTS: tuple[tuple[FailureClass, tuple[str, ...]], ...] = (
    (
        FailureClass.IAM,
        ("permission", "denied", "forbidden", "unauthorized", "accessdenied", "403"),
    ),
    (FailureClass.QUOTA, ("quota", "limit exceeded", "resourceexhausted", "throttl", "429")),
    (FailureClass.NETWORK, ("network", "timeout", "unreachable", "connection", "dns", "egress")),
    (FailureClass.PACKAGE, ("modulenotfound", "importerror", "no matching distribution", "wheel")),
    (FailureClass.CONTAINER, ("image", "manifest unknown", "pull", "exec format", "oom")),
    (FailureClass.DATA, ("column", "schema", "dtype", "empty", "null", "contract")),
)


def classify_failure(exc: BaseException) -> FailureClass:
    """例外を failure_class に落とす。分類不能は SDK。

    ここで None を返さないのは、`failure_class` が空の行が増えると
    permission friction クエリの分母が壊れるため。
    """
    text = f"{type(exc).__name__}: {exc}".lower()
    for failure_class, hints in _FAILURE_HINTS:
        if any(hint in text for hint in hints):
            return failure_class
    return FailureClass.SDK


@runtime_checkable
class RunSink(Protocol):
    """記録先。**5基盤×2到達経路の交差点だけ**を契約にする。

    Protocol にしているのは、Neon 到達不能時に JSONL fallback へ差し替えられる
    ようにするため（到達経路そのものが比較軸）。実装は NeonRunSink（direct）と
    JsonlRunSink（collected）の2つで、**両方が意味を持って実装できる操作だけ**を置く:

        record_run    … 1試行を1行残す（両経路の存在理由）
        next_attempt  … 同一 (platform, stage) の過去行数 + 1（friction 計測の土台）

    ## この契約を太らせないこと（2026-08-02 の事故2件から）

    かつて `merge_run_params` がここにあった。実装が意味を持つのは Neon だけ
    （JSONL は追記専用で「既存行の更新」が定義できない）なのに必須契約にした結果、
    JsonlRunSink に no-op・decorator に中継が生え、**1つの修正が全 sink と
    5基盤の共有基底に波及する**構造になった。片実装の操作は契約ではなく、
    それを必要とする機能の側（resume → `platforms/shared/resume.py`）に置く。
    契約の形は `tests/core/test_sink_contract.py` が **メソッド集合ごと** pin している。
    """

    def record_run(self, run: MlRun) -> WritePath: ...

    def next_attempt(self, platform: Platform, stage: Stage) -> int: ...


@dataclass
class RunContext:
    """with ブロック内で呼び出し側が埋めるフィールド。"""

    run_id: str
    platform: Platform
    stage: Stage
    metrics: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    failure_class: FailureClass | None = None


@contextmanager
def track(
    platform: Platform,
    stage: Stage,
    *,
    sink: RunSink,
    attempt: int | None = None,
    run_id: str | None = None,
) -> Iterator[RunContext]:
    """1試行を必ず ml_runs へ1行残す。

    成功・失敗のどちらでも記録する。記録に失敗しても本処理は止めない
    （記録の失敗で学習を落とすと、比較対象の試行そのものが消える）。
    """
    tier, unification_unit = PLATFORM_TIER[platform]
    # revision は yield の**前**に解決する。finally 内で初めて解決すると、
    # 解決失敗（stamp 忘れ等）が本来の例外をマスクして原因を潰す。
    revision = code_revision()
    ctx = RunContext(
        run_id=run_id or str(uuid.uuid4()),
        platform=platform,
        stage=stage,
    )
    resolved_attempt = attempt if attempt is not None else sink.next_attempt(platform, stage)
    started = time.perf_counter()
    status = Status.SUCCESS
    failure_class: FailureClass | None = None
    error_excerpt: str | None = None

    try:
        yield ctx
    except BaseException as exc:
        status = Status.FAILURE
        failure_class = ctx.failure_class or classify_failure(exc)
        error_excerpt = f"{type(exc).__name__}: {exc}"[:ERROR_EXCERPT_MAX_CHARS]
        raise
    finally:
        run = MlRun(
            run_id=ctx.run_id,
            platform=platform,
            tier=tier,
            unification_unit=unification_unit,
            stage=stage,
            status=status,
            attempt=resolved_attempt,
            duration_seconds=time.perf_counter() - started,
            failure_class=failure_class if status is Status.FAILURE else FailureClass.NONE,
            error_excerpt=error_excerpt,
            code_revision=revision,
            # 実際の到達経路は sink が確定する（Neon 直 = direct / JSONL 退避 = collected）
            write_path=WritePath.DIRECT,
            metrics=ctx.metrics,
            params=ctx.params,
        )
        try:
            sink.record_run(run)
        except Exception:  # noqa: BLE001 - 記録失敗で本処理を落とさない
            _logger.exception("計測の記録に失敗（run_id=%s）", ctx.run_id)
