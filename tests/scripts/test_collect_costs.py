"""コスト収集（scripts/collect_costs.py）の検証。

**実クラウドを叩かない。** 核心は「**0 円と取得不能を混ぜない**」——
混ぜると比較表のコスト列が嘘になる。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from tests.conftest import load_script

costs = load_script("collect_costs")


class FakeCostExplorer:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.requests: list[dict[str, Any]] = []

    def get_cost_and_usage(self, **kwargs: Any) -> dict[str, Any]:
        self.requests.append(kwargs)
        return self.payload


COST_PAYLOAD = {
    "ResultsByTime": [
        {
            "TimePeriod": {"Start": "2026-07-30", "End": "2026-07-31"},
            "Groups": [
                {"Keys": ["Amazon SageMaker"], "Metrics": {"UnblendedCost": {"Amount": "1.25"}}},
                {"Keys": ["Amazon S3"], "Metrics": {"UnblendedCost": {"Amount": "0"}}},
            ],
        }
    ]
}


def test_cost_explorer_rows_are_daily_by_service() -> None:
    ce = FakeCostExplorer(COST_PAYLOAD)

    rows = costs.collect_sagemaker({"ce": ce}, days=7, today=date(2026, 8, 1))

    assert [(r.usage_date, r.service, r.amount_usd) for r in rows] == [
        ("2026-07-30", "Amazon SageMaker", 1.25)
    ]
    # 0 円の行は落とす（cost_snapshots を無意味な行で埋めない）
    assert all(r.amount_usd > 0 for r in rows)
    assert ce.requests[0]["TimePeriod"] == {"Start": "2026-07-25", "End": "2026-08-01"}


def test_unavailable_platform_is_reported_not_zero_filled() -> None:
    """取得経路が無い基盤を 0 円として記録しない（比較表が嘘になる）。"""
    rows, unavailable = costs.collect(
        ["vertex", "sagemaker"],
        days=7,
        clients={"sagemaker": {"ce": FakeCostExplorer(COST_PAYLOAD)}},
        today=date(2026, 8, 1),
    )

    assert [r.platform for r in rows] == ["sagemaker"]
    assert any(note.startswith("vertex:") for note in unavailable)


def test_collector_failure_does_not_stop_other_platforms() -> None:
    class Exploding:
        def get_cost_and_usage(self, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("AccessDeniedException: ce:GetCostAndUsage")

    rows, unavailable = costs.collect(
        ["sagemaker", "snowflake"],
        days=7,
        clients={"sagemaker": {"ce": Exploding()}, "snowflake": {"session": None}},
        today=date(2026, 8, 1),
    )

    assert rows == []
    assert len(unavailable) == 2
    assert any("AccessDenied" in note for note in unavailable)


def test_snowflake_converts_credits_with_explicit_price() -> None:
    class FakeSession:
        def sql(self, statement: str) -> Any:
            rows = [("2026-07-30", "MCML_DEV_WH", 1.5)]
            return type("R", (), {"collect": staticmethod(lambda: rows)})()

    rows = costs.collect_snowflake(
        {"session": FakeSession()}, days=7, today=date(2026, 8, 1), credit_price_usd=3.0
    )

    assert rows[0].service == "warehouse:MCML_DEV_WH"
    assert rows[0].amount_usd == pytest.approx(4.5)


def test_date_range_is_half_open() -> None:
    assert costs.date_range(7, today=date(2026, 8, 1)) == ("2026-07-25", "2026-08-01")
