"""sql/ の正本2ファイルの整合を pin する（「正本1箇所 + 参照側を pin」の型）。

ローカルに PostgreSQL が無いため実行はできない。ここで見るのは:

  - comparison_queries.sql が TODO のまま逆戻りしていないこと
    （UC-003「SELECT だけで比較できる」の正本が空になると、レポートが手集計に退化する）
  - クエリが参照するテーブルが schema.sql に実在すること
  - 比較軸のキー（rmse / attempt / failure_class / write_path / residual_resources）が
    クエリに載っていること

実 PostgreSQL 上での実行確認は Phase 1 の Neon 到達時に行う
（仕様準拠監査 2026-08-01 の Verification 項目）。
"""

from __future__ import annotations

import re

from tests.conftest import REPO_ROOT

SQL_DIR = REPO_ROOT / "sql"
SCHEMA = (SQL_DIR / "schema.sql").read_text(encoding="utf-8")
QUERIES = (SQL_DIR / "comparison_queries.sql").read_text(encoding="utf-8")


def _schema_tables() -> set[str]:
    return set(re.findall(r"create table if not exists (\w+)", SCHEMA))


def _cte_names() -> set[str]:
    """`with <name> as (` で定義した中間名。実テーブルではない。"""
    return set(re.findall(r"\bwith\s+(\w+)\s+as\s*\(", QUERIES, flags=re.IGNORECASE))


def _referenced_tables() -> set[str]:
    referenced = set(re.findall(r"\bfrom\s+(\w+)", QUERIES, flags=re.IGNORECASE))
    return referenced - _cte_names()


def test_queries_are_implemented_not_todo() -> None:
    assert "TODO" not in QUERIES, "comparison_queries.sql が TODO に戻っている"
    assert QUERIES.lower().count("select") >= 5, "定番4本 + 補助1本が揃っていること"


def test_referenced_tables_exist_in_schema() -> None:
    unknown = _referenced_tables() - _schema_tables() - _schema_views()
    assert not unknown, f"schema.sql に無いテーブル/ビューを参照: {sorted(unknown)}"


def test_comparison_axes_are_covered() -> None:
    """5つの比較軸キーがクエリに現れること（軸の欠落 = レポートの列の欠落）。"""
    for key in ("rmse", "attempt", "failure_class", "write_path", "residual_resources"):
        assert key in QUERIES, f"比較軸 {key} を引くクエリが無い"


def test_schema_has_the_three_tables() -> None:
    """05_data_model の3テーブル分離が保たれていること。"""
    assert _schema_tables() >= {"ml_runs", "infra_events", "cost_snapshots"}


# --- teardown 行の分離（2026-08-01 追加）----------------------------------
#
# teardown は 2026-08-01 まで `stage='deploy'` + `params.action='teardown'` で
# 記録されていた。過去行は書き換えない規約なので、クエリ側で両形式を扱う。


def _statements() -> list[str]:
    """コメントを除いた SELECT 文のリスト。"""
    without_comments = "\n".join(
        line for line in QUERIES.splitlines() if not line.lstrip().startswith("--")
    )
    return [s.strip() for s in without_comments.split(";") if s.strip()]


def test_attempt_and_write_path_queries_exclude_legacy_teardown() -> None:
    """attempt / write_path を数えるクエリが旧形式の teardown 行を弾くこと。

    弾かないと deploy の attempts_until_success に撤退の試行が混ざり、
    permission friction（本命の計測値）の意味が変わる。
    """
    for statement in _statements():
        counts_attempt = "attempt" in statement
        counts_write_path = "write_path" in statement and "group by" in statement.lower()
        if not (counts_attempt or counts_write_path):
            continue
        assert "'teardown'" in statement, (
            f"teardown を除外していないクエリがある:\n{statement[:200]}"
        )


def test_duration_query_reports_teardown_as_its_own_stage() -> None:
    """所要時間クエリが teardown を deploy と混ぜず独立の stage として出すこと。"""
    duration_statements = [s for s in _statements() if "duration_seconds" in s and "avg" in s]

    assert duration_statements, "stage 別所要のクエリが無い"
    for statement in duration_statements:
        assert "case when" in statement.lower() and "'teardown'" in statement, (
            "旧形式の teardown を独立 stage として読み替えていない"
        )


def test_metric_parity_query_does_not_group_by_code_revision() -> None:
    """parity クエリが code_revision で束ねないこと。

    5基盤を順に回すと SHA は基盤ごとに変わる（2026-08-01 実測）。
    SHA で group by すると「1 SHA = 1 基盤」に割れて parity を示せない。
    """
    parity = next(s for s in _statements() if "distinct_rmse" in s)

    assert "group by code_revision" not in parity.lower(), (
        "parity クエリが code_revision で group by している（基盤ごとに SHA が違うため割れる）"
    )


# --- 比較母集団（2026-08-02 追加）------------------------------------------


def _schema_views() -> set[str]:
    return set(re.findall(r"create or replace view (\w+)", SCHEMA))


def test_queries_read_only_the_baseline_views() -> None:
    """比較クエリは生の ml_runs / infra_events を読まない。

    テーブルは追記専用なので、campaign 後の検証 run・実験 run が増えるたびに
    生テーブル直読みの数字は変わる。2026-08-02 の再構築検証で実際に汚染が起き、
    G3「クエリをそのまま流せばレポートの表が再現できる」が破れた。
    母集団の定義（baseline_* view）を経由することで、後から行が増えても
    レポートの数字が動かない。
    """
    raw = {"ml_runs", "infra_events"} & _referenced_tables()

    assert not raw, f"比較クエリが生テーブルを読んでいる: {sorted(raw)}"
    assert {"baseline_runs", "baseline_infra_events"} <= _referenced_tables()


def test_baseline_views_are_defined_in_schema() -> None:
    assert {"baseline_runs", "baseline_infra_events"} <= _schema_views()


def test_baseline_boundary_is_single_valued() -> None:
    """境界 timestamp は2つの view で同一の1値。ずれると母集団が2つに割れる。"""
    boundaries = set(re.findall(r"created_at < timestamptz '([^']+)'", SCHEMA))

    assert len(boundaries) == 1, f"境界がぶれている: {sorted(boundaries)}"
    # baseline 最終行 12:03Z と最初の campaign 後 run 17:17Z の間にあること（実測の事実）
    assert boundaries == {"2026-08-01 15:00:00+00"}
