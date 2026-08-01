"""`RunSink` 契約の適合テスト。**sink / decorator を足すたびに開く穴を塞ぐ番人。**

2026-08-02 の事故が動機。`merge_run_params` は契約に無く
`getattr(sink, "merge_run_params", None)` の文字列一致で拾われていたため、
それを持たない decorator を1枚挟んだだけで**静かに何もせず戻り**、
学習成功行の `params` が空 = stage を跨いだ再開が壊れた。
ユニットテストは全て緑のまま、実クラウドで初めて露見した。

このテストは「実装を1つ足したら適合も1つ増える」形にしてある。
新しい sink を足して契約を満たしていなければ、ここが落ちる。

なお Experiments 複写はこの後 **observer へ作り替えて記録経路から降ろした**
（最終形は `VertexAdapter._tracked` の override 内。共通層に observer は存在しない）。
sink の契約を持たない層は、契約を落としようがない。
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from core.telemetry.tracking import RunSink

# 本リポジトリの sink 実装すべて。**新しく足したらここへ追記する**
# （追記を忘れても下の「取りこぼし検出」テストが気付く）。
SINK_IMPORTS = (
    ("core.telemetry.sinks", "JsonlRunSink"),
    ("platforms.neon.run_sink", "NeonRunSink"),
    ("platforms.shared.contracts.tracking", "RecordingSink"),
)


def load(module_name: str, class_name: str) -> type:
    module = pytest.importorskip(module_name)
    return getattr(module, class_name)


SPINE_METHODS = ("record_run", "next_attempt")


def test_the_contract_is_pinned_to_the_intersection() -> None:
    """RunSink の公開メソッド集合を**ごと** pin する。

    契約を太らせる変更（メソッド追加）は、この pin を意図的に書き換えないと通らない。
    かつて merge_run_params（実装が意味を持つのは Neon だけ）を必須化し、
    全 sink に no-op / 中継が生えて1修正が全体へ波及した。片実装の操作は
    契約ではなく機能側（resume.persist_handoff）に置く。
    """

    public = {
        name
        for name, member in inspect.getmembers(RunSink)
        if not name.startswith("_") and callable(member)
    }

    assert public == set(SPINE_METHODS), f"RunSink 契約が交差点からずれた: {sorted(public)}"


@pytest.mark.parametrize(("module_name", "class_name"), SINK_IMPORTS, ids=lambda v: v)
def test_every_sink_satisfies_the_protocol(module_name: str, class_name: str) -> None:
    """スパイン2メソッドを全 sink が実装していること。"""
    sink_class = load(module_name, class_name)

    missing = [
        name for name in SPINE_METHODS if not callable(getattr(sink_class, name, None))
    ]

    assert not missing, f"{class_name} が RunSink 契約を満たさない（欠落: {missing}）"


def test_no_sink_implementation_is_left_out_of_this_test() -> None:
    """`SINK_IMPORTS` への追記漏れを検出する。

    一覧を手で持つ以上、**忘れたときに気付けなければ意味がない**。
    `record_run` を定義しているクラスを src 全体から拾って突き合わせる。
    """
    src = Path(__file__).resolve().parents[2] / "src"
    found: set[str] = set()
    for path in src.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        current: str | None = None
        for line in text.splitlines():
            if line.startswith("class "):
                current = line.removeprefix("class ").split("(")[0].split(":")[0].strip()
            if line.strip().startswith("def record_run(") and current:
                found.add(current)

    # Protocol 自身は実装ではない
    found.discard("RunSink")
    found.discard("RunWriter")

    listed = {name for _, name in SINK_IMPORTS}
    assert found <= listed, f"RunSink 実装が SINK_IMPORTS に無い: {sorted(found - listed)}"


def test_protocol_is_runtime_checkable() -> None:
    """`isinstance` で検査できること（適合テストの土台）。"""
    assert getattr(RunSink, "_is_runtime_protocol", False)
