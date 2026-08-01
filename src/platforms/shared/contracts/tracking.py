"""5基盤 adapter が共有する「1操作 = 1 ml_runs 行」の実装。

A-1〜A-5 で同じ `_RecordingSink` と `_tracked()` を5回書いたので、ここへ집約する
（reuse-asset-import-map.md A-6）。**共通化するのはここだけ**にする理由:

  - 記録の形（必ず1行・失敗も記録・例外を投げない）は **5基盤で同一でなければ
    比較できない**。ばらつくと attempt / failure_class の意味が基盤ごとに変わる。
  - 逆に、SDK 呼び出しの形は基盤ごとに違うことが比較材料なので **共通化しない**。
    ここで `submit_training()` の中身まで抽象化すると、見たい差が隠れる。

`platforms/contracts/ports.py` の「port を切る基準」と同じ判断に立っている。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

from core.telemetry.schemas import MlRun, Platform, Stage, Status
from core.telemetry.tracking import RunContext, RunSink, track

# ジョブへ引き渡す Neon 接続情報の環境変数名（platforms.neon.connection と同じ）。
# **秘密なので config.yaml には置かない。** オーケストレータ側の環境（Doppler）から
# ジョブ定義の env へ載せ替える。
NEON_POOLED_URI_ENV = "NEON_MULTICLOUD_POOLED_URI"


def telemetry_env() -> dict[str, str]:
    """学習ジョブへ渡す環境変数（Neon 接続情報）。

    ジョブの中から Neon へ直接 INSERT する経路（write_path='direct'）に必要。
    未設定なら空を返し、ジョブは JSONL fallback に落ちる（= collected。
    到達不能そのものが比較データなので、ここで例外にしない）。

    ⚠️ この値はジョブ定義に載るため、各基盤のコンソール / Describe API から
    読める。本ラボの範囲では許容するが、本番設計では各基盤の secret manager
    （Secret Manager / Secrets Manager / Key Vault）参照に置き換えること。

    **CODE_REVISION は渡さない。** コンテナにはビルド時に焼き込んだ値があり、
    オーケストレータの git HEAD で上書きすると「実際に動いたコード」と
    記録がずれる（比較の担保そのものが壊れる）。
    """
    uri = os.environ.get(NEON_POOLED_URI_ENV)
    if not uri:
        return {}
    return {NEON_POOLED_URI_ENV: uri}


class RecordingSink:
    """track() が組み立てた MlRun を掴むためのラッパ。

    track() は sink へ渡すだけで MlRun を返さないが、PlatformAdapter の契約は
    「各メソッドが MlRun を返す」。core を変えずに両立させるため、
    sink 側で最後の1行を保持する。

    `suppress_success=True` のときは成功行を**書かない**（保持だけする）。
    学習の成功行はジョブ側（platforms.neon.job_record）が書く規約のため
    （下の `TrackedOperations._tracked` の job_owns_success を参照）。
    """

    def __init__(self, inner: RunSink, *, suppress_success: bool = False) -> None:
        self._inner = inner
        self._suppress_success = suppress_success
        self.last: MlRun | None = None

    def record_run(self, run: MlRun) -> Any:
        self.last = run
        if self._suppress_success and run.status is Status.SUCCESS:
            return run.write_path
        return self._inner.record_run(run)

    def next_attempt(self, platform: Platform, stage: Stage) -> int:
        return self._inner.next_attempt(platform, stage)


class TrackedOperations:
    """adapter の基底。`platform` と `_sink` を持つクラスに `_tracked()` を与える。

    継承側は `platform: Platform` クラス属性と `self._sink` を用意する。
    """

    platform: Platform
    _sink: RunSink

    def _next_attempt(self, stage: Stage) -> int:
        """この試行の attempt を先に確定させる。

        `submit_training` では **ジョブへ渡す**必要がある（ジョブ側は Neon へ
        届かない可能性があり、そこで数え直せない）。adapter 側の記録とも
        同じ値を使い、1試行 = 1 attempt を保つ。
        """
        return self._sink.next_attempt(self.platform, stage)

    def _tracked(
        self,
        stage: Stage,
        params: dict[str, Any],
        call: Callable[[RunContext], None],
        *,
        attempt: int | None = None,
        run_id: str | None = None,
        job_owns_success: bool = False,
    ) -> MlRun:
        """1操作 = 1 ml_runs 行。失敗しても記録済みの MlRun を返す。

        例外を再送出しないのは ports.PlatformAdapter の契約
        （失敗も比較データ。呼び出し側は status / failure_class で判定する）。
        黙って落ちると原因究明が遅れるのでログには出す。

        `job_owns_success=True`（学習投入で使う）のとき、**成功行はここで書かない**。
        ジョブの中から書かれる行（platforms.neon.job_record）が正であり、
        そこにしか「ジョブから Neon へ届いたか」= write_path の真値が無いため。
        投入自体が失敗した場合はジョブが起動していないので、ここが唯一の記録者になる。
        """
        sink = RecordingSink(self._sink, suppress_success=job_owns_success)
        try:
            with track(self.platform, stage, sink=sink, attempt=attempt, run_id=run_id) as ctx:
                ctx.params.update(params)
                call(ctx)
        except Exception:
            logging.getLogger(f"platforms.{self.platform.value}").warning(
                "%s に失敗（run は記録済み）", stage.value, exc_info=True
            )
        if sink.last is None:  # pragma: no cover - track が必ず記録する
            raise RuntimeError("track() が MlRun を記録しなかった")
        if job_owns_success and sink.last.status is Status.SUCCESS:
            self._merge_job_row_params(sink.last)
        return sink.last

    def _merge_job_row_params(self, run: MlRun) -> None:
        """ジョブが書いた成功行へ、adapter しか知らない params を足す。

        `model_artifact_uri` は **投入した adapter にしか分からない**
        （ジョブ側は自分の成果物がどの URI で参照されるかを知らない）。
        ここで足さないと `ml_runs` の学習成功行は `params={}` のままになり、
        register / deploy を単体で再開できず **中断のたびに train からやり直し**になる
        （2026-08-01 に Azure で実際に踏んだ。docs/comparison/04_azureml.md）。

        **telemetry は非致命**（docs/06_error_policy.md）。足せなくても
        adapter の結果は変えない。行がまだ無い（collected 経路で `make collect`
        待ち）ケースも異常ではないので、静かに 0 件で終わる。
        """
        merge = getattr(self._sink, "merge_run_params", None)
        if merge is None or not run.params:
            return
        try:
            merge(run.run_id, dict(run.params))
        except Exception:  # noqa: BLE001 - 記録の失敗で学習結果を変えない
            logging.getLogger(f"platforms.{self.platform.value}").warning(
                "run %s の params を足せなかった", run.run_id, exc_info=True
            )


# 「見つからない」を表す SDK 側の言い回し。ここに無い例外は**確認不能**として扱う。
_MISSING_HINTS = (
    "not found",
    "notfound",
    "does not exist",
    "resource_does_not_exist",
    "validationexception",
    "resourcenotfound",
    "404",
)


def is_missing(exc: BaseException) -> bool:
    """例外が「対象が存在しない」を意味するかを判定する。

    **権限不足・API 無効を「存在しない」と混同しない**ための関門。
    混同すると teardown が「消すものが無かった」と成功を報告し、
    実際には残ったまま課金が続く（scripts/check_residual.py が ERROR を
    黙らせないのと同じ理由。docs/02_architecture.md の残留比較が壊れる）。

    判定できない例外は False を返し、呼び出し側で送出させる → 失敗として記録される。
    """
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(hint in text for hint in _MISSING_HINTS)


def artifact_uri_from(train_run: MlRun) -> str:
    """train の MlRun から成果物 URI を取り出す（5基盤で同じキーを使う）。"""
    uri = train_run.params.get("model_artifact_uri")
    if not uri:
        raise ValueError("train run に model_artifact_uri が無い（学習が失敗している）")
    return str(uri)
