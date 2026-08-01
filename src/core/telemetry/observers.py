"""run を**観測するだけ**の層（記録の正本には触らない）。

## なぜ sink と分けるか（2026-08-02 の再設計）

`ml_runs` の行には**所有者の規約**がある（`platforms/neon/job_record.py`）:

    学習の成功行 … ジョブ側が書く（write_path の真値はそこにしか無い）
    それ以外     … adapter 側が書く

この規約は `RecordingSink(suppress_success=True)` で実装されている。
問題は、Experiments 複写のような**副次的な観測**を sink の decorator として足すと、
「Neon へ書かない」が「観測もしない」に化けることだった。実際、
学習成功行 —— 最も情報量の多い行 —— が Experiments に一切現れなかった（実クラウドで実測）。

**「誰が正本を書くか」と「誰が run を見るか」は別の関心事**である。
分けると次の2つが同時に解ける:

  1. 観測は抑制の影響を受けない（学習成功行も観測できる）
  2. 観測が**記録経路から降りる**（sink の契約を落として再開を壊す事故が構造的に起きない。
     実際 2026-08-02 に decorator が `merge_run_params` を落として起きた）

## 何を観測できて、何をできないか（境界を明示する）

観測できるのは **adapter が知っている run** である。学習成功行の場合、
`metrics`（RMSE 等）は**ジョブが書いた Neon の行にしか無い**。学習コンテナは
依存最小の制約で Vertex SDK を持てず（`docker/training/Dockerfile`）、
ジョブ側から観測させることもできない。

したがって学習成功行の観測は `attempt` / `status` / `duration_seconds`（投入〜完了の待ち時間）/
`params`（`model_artifact_uri` 等）までで、**モデル品質の指標は含まれない**。
これは実装の不足ではなく**依存最小の制約から来る境界**なので、ここに書いて隠さない。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from core.telemetry.schemas import MlRun


@runtime_checkable
class RunObserver(Protocol):
    """1 run を観測する。**戻り値を持たず、例外を投げない**のが契約。

    観測は計測ではないので、失敗しても adapter の結果を変えてはいけない
    （`docs/06_error_policy.md`「telemetry は非致命」）。
    実装側で握り潰し、ログにだけ残す。
    """

    def observe(self, run: MlRun) -> None: ...


class NullObserver:
    """既定。何もしない。

    `None` チェックを呼び出し側に散らかさないために置く
    （`if observer is not None` が増えると、観測の有無で分岐が生えて読みにくくなる）。
    """

    def observe(self, run: MlRun) -> None:
        return None
