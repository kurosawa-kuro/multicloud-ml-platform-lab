"""層の向きの番人 —— 「1つの修正が5基盤へ波及する」経路を機械的に塞ぐ。

02_architecture「設計の導出順序」手順3の帰結:

  - 共有契約層（platforms/shared/contracts）は**基盤を知らない**。
    知った瞬間、その基盤の都合の変更が5基盤の共有面に波及する
  - 基盤 adapter は**他の基盤を知らない**。横に依存した瞬間、1基盤の変更が
    別の基盤を壊せるようになる
  - 例外は composition root（factory）と driver（run_phase）だけ。
    5実装を並べて選ぶのが仕事なので、全基盤を import してよい

core 側の向き（core → platforms 禁止）は tests/core/test_core_boundaries.py が守る。
ここはその platforms 内版。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from tests.conftest import REPO_ROOT

PLATFORMS_DIR = REPO_ROOT / "src" / "platforms"
PLATFORM_PACKAGES = ("vertex", "sagemaker", "azureml", "databricks", "snowflake")


def imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def platform_imports(path: Path) -> set[str]:
    return {
        name
        for name in imports_of(path)
        if any(name.startswith(f"platforms.{pkg}") for pkg in PLATFORM_PACKAGES)
    }


@pytest.mark.parametrize(
    "path",
    sorted((PLATFORMS_DIR / "shared" / "contracts").rglob("*.py")),
    ids=lambda p: p.name,
)
def test_shared_contracts_know_no_platform(path: Path) -> None:
    """契約層が基盤名を知らないこと。ここが破れると契約変更が全基盤に波及する。"""
    assert platform_imports(path) == set(), f"{path.name} が基盤を import している"


@pytest.mark.parametrize("platform", PLATFORM_PACKAGES)
def test_adapters_do_not_import_each_other(platform: str) -> None:
    """基盤 adapter が横の基盤を知らないこと。"""
    others = set(PLATFORM_PACKAGES) - {platform}
    for path in sorted((PLATFORMS_DIR / platform).rglob("*.py")):
        crossing = {
            name
            for name in platform_imports(path)
            if any(name.startswith(f"platforms.{other}") for other in others)
        }
        assert crossing == set(), f"{platform}/{path.name} が他基盤を import: {sorted(crossing)}"


def test_shared_modules_import_all_platforms_or_none() -> None:
    """shared が基盤を知ってよいのは **5基盤そろいの composition root** としてだけ。

    factory（adapter 登録簿）と config（Config 型の登録簿）は5基盤を並べて選ぶのが
    仕事なので全 import が正当。危険なのは**部分 import**（1〜4基盤だけ知る）で、
    それは単一基盤の都合が共有層へ漏れ始めたサイン —— 観測フックを共通層に置いて
    2度作り直した事故（decision-log 2026-08-02）と同型。
    neon は計測インフラであり比較対象の基盤ではないので数えない。
    """
    for path in sorted((PLATFORMS_DIR / "shared").glob("*.py")):
        touched = {
            pkg
            for pkg in PLATFORM_PACKAGES
            if any(name.startswith(f"platforms.{pkg}") for name in platform_imports(path))
        }
        assert len(touched) in (0, len(PLATFORM_PACKAGES)), (
            f"{path.name} が一部の基盤だけを import している（単一基盤の漏れ）: {sorted(touched)}"
        )
