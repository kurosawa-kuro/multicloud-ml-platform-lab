"""5基盤のコストを集めて cost_snapshots へ入れる。

**コストは実行時に確定しない**（請求反映に1〜2日遅れる）ので run とは別テーブル
にしてある（docs/02_architecture.md「計測データ層」）。このスクリプトは
フェーズ完了の翌日以降に回して、後追いで埋める用途。

移植元: ML/kaggle-bronze-gcp/src/runner/ops/costs.py（ジョブ単位のコスト概算記録）+
ML/eks-app-mlops-platform/apps/aws-resource-monitor（Cost Explorer の月次×サービス粒度）。
docs/tasks/02_backlog/reuse-asset-import-map.md C。

**各基盤で取得経路がまったく違う**こと自体が比較材料:

    Vertex     : Cloud Billing の BigQuery export（未設定なら取得不能 = 記録する）
    SageMaker  : Cost Explorer `ce:GetCostAndUsage`（サービス粒度で取れる）
    Azure ML   : Cost Management の Query API
    Databricks : system.billing.usage（**SQL で取れる** = Tier B の強み）
    Snowflake  : WAREHOUSE_METERING_HISTORY（クレジット。USD 換算は単価が要る）

取得できなかった基盤は**黙って 0 にしない**。空の結果を返し、呼び出し側が
「取得できなかった」と分かる形にする（0 と混ぜると比較表が嘘になる）。

    doppler run -- python scripts/collect_costs.py --platform sagemaker --days 7
    doppler run -- python scripts/collect_costs.py --all --days 7 --record
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

PLATFORMS = ("vertex", "sagemaker", "azureml", "databricks", "snowflake")

# Snowflake はクレジット建て。USD 換算の単価は契約で変わるので既定を置き、
# 実際の単価は --credit-price で上書きする（推測値を混ぜない）。
DEFAULT_SNOWFLAKE_CREDIT_USD = 2.0


@dataclass(frozen=True)
class CostRow:
    """cost_snapshots 1行ぶん（platform / usage_date / service / amount_usd）。"""

    platform: str
    usage_date: str
    service: str
    amount_usd: float

    def as_record(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "usage_date": self.usage_date,
            "service": self.service,
            "amount_usd": self.amount_usd,
        }


def date_range(days: int, today: date | None = None) -> tuple[str, str]:
    """[start, end) を ISO 文字列で返す。end は排他（Cost Explorer の流儀に合わせる）。"""
    end = today or date.today()
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()


# --- 各基盤 ---------------------------------------------------------------


def collect_sagemaker(
    clients: dict[str, Any] | None = None, *, days: int = 7, today: date | None = None
) -> list[CostRow]:
    """Cost Explorer から日次×サービス粒度で取る。

    `ce:GetCostAndUsage` は他4基盤と比べて**そのまま比較に使える粒度**で返る。
    """
    clients = clients or {"ce": _boto3("ce")}
    start, end = date_range(days, today)
    response = clients["ce"].get_cost_and_usage(
        TimePeriod={"Start": start, "End": end},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )
    rows = []
    for period in response.get("ResultsByTime", []):
        usage_date = period["TimePeriod"]["Start"]
        for group in period.get("Groups", []):
            amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
            if amount <= 0:
                continue
            rows.append(CostRow("sagemaker", usage_date, group["Keys"][0], round(amount, 6)))
    return rows


def collect_databricks(
    clients: dict[str, Any] | None = None, *, days: int = 7, today: date | None = None
) -> list[CostRow]:
    """system.billing.usage を SQL で引く。

    **Tier B は課金明細も SQL で取れる**（Tier A は専用 API が要る）。
    この差は「データ基盤内蔵型」の性格そのもの。
    """
    clients = clients or {"workspace": _databricks_workspace()}
    start, end = date_range(days, today)
    statement = (
        "select usage_date, sku_name, sum(usage_quantity) as dbus "
        "from system.billing.usage "
        f"where usage_date >= '{start}' and usage_date < '{end}' "
        "group by usage_date, sku_name"
    )
    rows = []
    for row in _databricks_query(clients["workspace"], statement):
        # DBU 単価は契約で変わる。単価不明のまま USD へ丸めない
        rows.append(CostRow("databricks", str(row[0]), f"dbu:{row[1]}", float(row[2])))
    return rows


def collect_snowflake(
    clients: dict[str, Any] | None = None,
    *,
    days: int = 7,
    today: date | None = None,
    credit_price_usd: float = DEFAULT_SNOWFLAKE_CREDIT_USD,
) -> list[CostRow]:
    """WAREHOUSE_METERING_HISTORY からクレジットを取り、単価で USD 換算する。"""
    clients = clients or {"session": _snowpark_session()}
    start, end = date_range(days, today)
    statement = (
        "select to_date(start_time) as usage_date, warehouse_name, "
        "sum(credits_used) as credits "
        "from snowflake.account_usage.warehouse_metering_history "
        f"where start_time >= '{start}' and start_time < '{end}' "
        "group by 1, 2"
    )
    rows = []
    for row in clients["session"].sql(statement).collect():
        values = list(row) if not isinstance(row, dict) else list(row.values())
        credits = float(values[2])
        rows.append(
            CostRow(
                "snowflake",
                str(values[0]),
                f"warehouse:{values[1]}",
                round(credits * credit_price_usd, 6),
            )
        )
    return rows


def collect_vertex(
    clients: dict[str, Any] | None = None, *, days: int = 7, today: date | None = None
) -> list[CostRow]:
    """Cloud Billing の BigQuery export を引く。

    **export が未設定だと取得経路が無い**（コンソールの請求画面は API 化されていない）。
    その場合は空を返し、「取得不能」を呼び出し側が記録する。0 で埋めない。
    """
    if clients is None or "bigquery" not in clients:
        return []
    start, end = date_range(days, today)
    table = clients.get("billing_export_table")
    if not table:
        return []
    statement = (
        "select date(usage_start_time) as usage_date, service.description as service, "
        "sum(cost) as cost "
        f"from `{table}` "
        f"where usage_start_time >= '{start}' and usage_start_time < '{end}' "
        "group by 1, 2"
    )
    rows = []
    for row in clients["bigquery"].query(statement):
        rows.append(CostRow("vertex", str(row[0]), str(row[1]), round(float(row[2]), 6)))
    return rows


def collect_azureml(
    clients: dict[str, Any] | None = None, *, days: int = 7, today: date | None = None
) -> list[CostRow]:
    """Cost Management の Query API を引く。未配線なら空（Phase 4 で配線）。"""
    if clients is None or "cost_management" not in clients:
        return []
    start, end = date_range(days, today)
    result = clients["cost_management"].query(scope=clients.get("scope", ""), start=start, end=end)
    rows = []
    for entry in result:
        rows.append(
            CostRow(
                "azureml", str(entry["usage_date"]), str(entry["service"]), float(entry["cost"])
            )
        )
    return rows


COLLECTORS: dict[str, Callable[..., list[CostRow]]] = {
    "vertex": collect_vertex,
    "sagemaker": collect_sagemaker,
    "azureml": collect_azureml,
    "databricks": collect_databricks,
    "snowflake": collect_snowflake,
}


# --- クライアント解決（遅延 import）--------------------------------------


def _boto3(service: str) -> Any:
    import boto3

    return boto3.client(service)


def _databricks_workspace() -> Any:
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient()


def _databricks_query(workspace: Any, statement: str) -> Iterable[Any]:
    """SQL Warehouse 経由で1文実行する。戻りは行のイテラブル。"""
    response = workspace.statement_execution.execute_statement(
        statement=statement,
        warehouse_id=workspace.config.warehouse_id,
        wait_timeout="30s",
    )
    return getattr(getattr(response, "result", None), "data_array", []) or []


def _snowpark_session() -> Any:
    from snowflake.snowpark import Session

    return Session.builder.getOrCreate()


# --- 実行 -----------------------------------------------------------------


def collect(
    platforms: Iterable[str],
    *,
    days: int = 7,
    clients: dict[str, dict[str, Any]] | None = None,
    today: date | None = None,
) -> tuple[list[CostRow], list[str]]:
    """(取得できた行, 取得できなかった基盤) を返す。

    取得できなかった基盤を戻り値で分けるのは、**0 円と「取れなかった」を
    比較表で混ぜない**ため。
    """
    clients = clients or {}
    rows: list[CostRow] = []
    unavailable: list[str] = []
    for platform in platforms:
        try:
            found = COLLECTORS[platform](clients.get(platform), days=days, today=today)
        except Exception as exc:  # noqa: BLE001 - 1基盤の失敗で全体を止めない
            unavailable.append(f"{platform}: {type(exc).__name__}: {exc}")
            continue
        if not found:
            unavailable.append(f"{platform}: 取得経路なし（export 未設定 / 未配線）")
            continue
        rows.extend(found)
    return rows, unavailable


def record(rows: Iterable[CostRow]) -> int:
    """cost_snapshots へ入れる。同一 (platform, usage_date, service) は上書き。"""
    from core.telemetry.schemas import CostSnapshot, Platform
    from platforms.neon.run_sink import NeonRunSink

    sink = NeonRunSink()
    written = 0
    for row in rows:
        written += sink.record_cost_snapshot(
            CostSnapshot(
                platform=Platform(row.platform),
                usage_date=row.usage_date,
                service=row.service,
                amount_usd=row.amount_usd,
            )
        )
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="collect_costs")
    parser.add_argument("--platform", choices=PLATFORMS)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--record", action="store_true", help="cost_snapshots へ書く")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if not args.all and not args.platform:
        parser.error("--platform か --all のどちらかが必要")

    targets = PLATFORMS if args.all else (args.platform,)
    rows, unavailable = collect(targets, days=args.days)

    if args.json:
        print(json.dumps([r.as_record() for r in rows], ensure_ascii=False, indent=2))
    else:
        for row in rows:
            print(f"{row.platform:11} {row.usage_date} {row.service:40} ${row.amount_usd:.4f}")
        print(f"-- {len(rows)} 行")
    for note in unavailable:
        print(f"[取得不能] {note}")

    if args.record and rows:
        print(f"cost_snapshots へ {record(rows)} 行")

    # 取得できなかった基盤があっても異常終了はしない（後追いで埋める運用のため）
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
