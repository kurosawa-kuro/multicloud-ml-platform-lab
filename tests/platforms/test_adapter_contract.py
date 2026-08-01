"""5基盤 adapter の**共通契約**。基盤ごとに書き分けない不変条件だけを置く。

このファイルが守るのは「比較が成立するための前提」:

  1. `PlatformAdapter` プロトコルを満たす
  2. どの操作も **ml_runs を必ず1行**残す（成功も失敗も）
     例外は `submit_training` の成功時 —— その行は**ジョブの中から**書かれる
     （下記「run 行の所有者」）
  3. 失敗しても **例外を投げず** failure_class 付きの MlRun を返す
  4. 同一 (platform, stage) の再試行で **attempt が増える**
  5. teardown は **対象が無くても成功**（撤退の冪等性）
  6. 記録に **platform / tier / unification_unit / code_revision** が必ず載る

## run 行の所有者（5基盤共通・platforms/neon/job_record.py が正本）

学習の**成功**行はジョブ側が書く。adapter（オーケストレータ）が書くと
write_path が「手元マシンから届いた」を意味してしまい、
「ジョブから Neon へ届くか」という比較軸が測れなくなるため。
投入自体が失敗した場合はジョブが起動していないので adapter が唯一の記録者になる。

ここがばらつくと `attempt` / `failure_class` の意味が基盤ごとに変わり、
permission friction クエリ（本命の比較軸）が成立しない。

基盤固有の呼び出し検証は `tests/test_<platform>_adapter.py` に置く。
**そちらは共通化しない** —— 差そのものが比較材料だから。
"""

from __future__ import annotations

from typing import Any

import pytest
from tests.fakes import AdapterCase, all_cases

from core.telemetry.schemas import FailureClass, Stage, Status
from core.telemetry.sinks import JsonlRunSink
from platforms.shared.contracts.ports import PlatformAdapter

CASES = all_cases()
CASE_IDS = [c.id for c in CASES]


@pytest.fixture(params=CASES, ids=CASE_IDS)
def case(request: pytest.FixtureRequest) -> AdapterCase:
    return request.param


def operations(adapter: Any, case: AdapterCase) -> list[tuple[str, Any]]:
    """5基盤で同じ順に呼べる操作列。引数の値だけが基盤で違う。"""
    return [
        ("submit_training", lambda: adapter.submit_training({"num_leaves": 31})),
        ("register_model", lambda: adapter.register_model(case.artifact_uri)),
        ("deploy", lambda: adapter.deploy(case.model_ref)),
        ("predict_one", lambda: adapter.predict_one({"MedInc": 8.3})),
        ("teardown", lambda: adapter.teardown()),
    ]


def test_satisfies_platform_adapter_protocol(case: AdapterCase, sink: JsonlRunSink) -> None:
    assert isinstance(case.make(sink), PlatformAdapter)


def test_declares_tier_and_unification_unit(case: AdapterCase, sink: JsonlRunSink) -> None:
    """tier / unification_unit は集計キー。欠けると Tier 別比較が壊れる。"""
    adapter = case.make(sink)
    assert adapter.platform is case.platform
    assert adapter.tier is not None
    assert adapter.unification_unit is not None


def test_every_operation_records_exactly_one_run(
    case: AdapterCase, sink: JsonlRunSink, recorded: Any
) -> None:
    """submit_training の成功を除き、1操作 = 1行。"""
    adapter = case.make(sink)
    expected = 0

    for name, call in operations(adapter, case):
        call()
        if name != "submit_training":  # 成功した学習の行はジョブ側が書く
            expected += 1
        assert len(recorded()) == expected, f"{case.id}.{name} の記録行数が合わない"


def test_successful_training_row_is_left_to_the_job(
    case: AdapterCase, sink: JsonlRunSink, recorded: Any
) -> None:
    """成功した学習の行を adapter が書かないこと。

    書いてしまうと write_path が「オーケストレータから届いた」になり、
    **ジョブから Neon へ届くか**という比較軸（本ラボの中心）が測れなくなる。
    返り値としては MlRun を受け取れる（PlatformAdapter の契約は維持）。
    """
    adapter = case.make(sink)

    run = adapter.submit_training({})

    assert run.status is Status.SUCCESS
    assert run.stage is Stage.TRAIN
    assert recorded() == [], f"{case.id}: 成功した train を adapter が記録している"


def test_failed_submission_is_recorded_by_the_adapter(
    case: AdapterCase, sink: JsonlRunSink, recorded: Any
) -> None:
    """投入が失敗したときは adapter が唯一の記録者（ジョブが起動していない）。

    iam / quota はここでしか観測できない = permission friction の一次データ。
    """
    adapter = case.make_failing(sink)

    run = adapter.submit_training({})

    assert run.status is Status.FAILURE
    assert len(recorded()) == 1, f"{case.id}: 投入失敗が記録されていない"


def test_failures_are_recorded_and_never_raised(
    case: AdapterCase, sink: JsonlRunSink, recorded: Any
) -> None:
    """例外を投げると permission friction が測れない（ports の契約）。"""
    adapter = case.make_failing(sink)

    for name, call in operations(adapter, case):
        run = call()  # 例外が飛んだらここで失敗する
        assert run.status is Status.FAILURE, f"{case.id}.{name} が失敗として記録されていない"
        assert run.failure_class is not None
        assert run.failure_class is not FailureClass.NONE
        assert run.error_excerpt

    assert len(recorded()) == len(operations(adapter, case))


def test_attempt_increases_on_retry(case: AdapterCase, sink: JsonlRunSink) -> None:
    """「最小権限で通るまでに何回直したか」がこの数字。

    投入失敗を繰り返す形で見る（記録された行を数えて次の attempt が決まるため、
    行が残る経路で検証する必要がある）。
    """
    adapter = case.make_failing(sink)

    first = adapter.submit_training({})
    second = adapter.submit_training({})

    assert (first.attempt, second.attempt) == (1, 2)


def test_teardown_is_idempotent(case: AdapterCase, sink: JsonlRunSink) -> None:
    """撤退は何度でも呼べる。対象が無いことは失敗ではない。"""
    adapter = case.make(sink)

    first = adapter.teardown()
    second = adapter.teardown()

    assert first.status is Status.SUCCESS
    assert second.status is Status.SUCCESS


def test_recorded_row_carries_comparison_keys(
    case: AdapterCase, sink: JsonlRunSink, recorded: Any
) -> None:
    """比較 SELECT が要求する列が全部載っていること。"""
    adapter = case.make(sink)
    adapter.register_model(case.artifact_uri)

    row = recorded()[0]
    assert row["platform"] == case.platform.value
    assert row["stage"] == Stage.REGISTER.value
    assert row["tier"] in {"A", "B"}
    assert row["unification_unit"] in {"container", "package"}
    assert row["code_revision"]
    # JSONL 経由は必ず collected（direct と混ざると到達経路の比較が壊れる）
    assert row["write_path"] == "collected"


def test_predict_one_failure_is_classified(case: AdapterCase, sink: JsonlRunSink) -> None:
    """推論だけ失敗した場合も分類が付く（デプロイ済みかの切り分けに使う）。"""
    adapter = case.make_failing(sink)

    run = adapter.predict_one({"MedInc": 8.3})

    assert run.stage is Stage.PREDICT
    assert run.status is Status.FAILURE
    assert run.failure_class is not None


def test_all_five_platforms_are_covered() -> None:
    """ケースの登録漏れ検出。1基盤でも抜けると比較表に穴が空く。"""
    assert {c.platform.value for c in CASES} == {
        "vertex",
        "sagemaker",
        "azureml",
        "databricks",
        "snowflake",
    }
