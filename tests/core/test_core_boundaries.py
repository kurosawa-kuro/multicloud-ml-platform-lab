"""core/ 全体の依存境界を機械的に固定する。

core は5基盤すべてに同一のものが配布される。境界が破れる = どこかの基盤で
core を書き換える羽目になる = 「同一SHA」の前提が崩れる。守る不変条件:

1. **core はクラウド SDK / DB ドライバを import しない**
   （Snowflake warehouse の Anaconda channel に psycopg3 は無い。
     Databricks ML Runtime との依存衝突も避ける）
2. **core は platforms を import しない**（層の向きは platforms → core のみ）
3. サブパッケージ別の third-party 許可リスト:
       core/ml        lightgbm / numpy / pandas / sklearn / pyarrow
       core/app       fastapi / starlette / pydantic + lightgbm / pandas / numpy
       core/telemetry **stdlib のみ**（どの基盤のどんな環境でも import が通る砦）
4. core/ml はプラットフォーム名で分岐しない

コメント・docstring は対象外（AST の import 文と if 文だけを見る）。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
from tests.conftest import REPO_ROOT

CORE = REPO_ROOT / "src" / "core"
STDLIB = set(sys.stdlib_module_names)

ALLOWED_THIRD_PARTY: dict[str, set[str]] = {
    "ml": {"lightgbm", "numpy", "pandas", "sklearn", "pyarrow"},
    "app": {"fastapi", "starlette", "pydantic", "lightgbm", "pandas", "numpy"},
    "telemetry": set(),
}

FORBIDDEN_PREFIXES = (
    "google",
    "boto3",
    "botocore",
    "azure",
    "azureml",
    "databricks",
    "snowflake",
    "psycopg",
    "sqlalchemy",
    "pyspark",
    "mlflow",
    "platforms",  # 層の逆流（core → platforms）を禁止
)


def _module_files() -> list[Path]:
    return sorted(p for p in CORE.rglob("*.py") if "__pycache__" not in p.parts)


def _subpackage(path: Path) -> str:
    rel = path.relative_to(CORE)
    return rel.parts[0] if len(rel.parts) > 1 else "__root__"


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_module_files_exist() -> None:
    """空振りテストにしない。core にファイルがあることを先に確認する。"""
    assert len(_module_files()) >= 25


@pytest.mark.parametrize("path", _module_files(), ids=lambda p: str(p.relative_to(CORE)))
def test_no_forbidden_import(path: Path) -> None:
    """クラウド SDK / DB ドライバ / platforms への依存が無いこと。"""
    forbidden = {r for r in _imported_roots(path) if r.startswith(FORBIDDEN_PREFIXES)}
    assert not forbidden, f"{path.name} が禁止依存を import: {sorted(forbidden)}"


@pytest.mark.parametrize("path", _module_files(), ids=lambda p: str(p.relative_to(CORE)))
def test_only_allowed_third_party(path: Path) -> None:
    """サブパッケージ別の許可リスト外に依存が増えていないこと。"""
    sub = _subpackage(path)
    allowed = ALLOWED_THIRD_PARTY.get(sub, set())
    unexpected = {
        r
        for r in _imported_roots(path)
        if r not in allowed and r not in STDLIB and r != "core" and not r.startswith("__")
    }
    assert not unexpected, f"core/{sub}/{path.name} に想定外の依存: {sorted(unexpected)}"


def test_telemetry_is_stdlib_only() -> None:
    """telemetry は全基盤で import が通る最後の砦。third-party ゼロを個別に固定する。"""
    for path in _module_files():
        if _subpackage(path) != "telemetry":
            continue
        third_party = {
            r
            for r in _imported_roots(path)
            if r not in STDLIB and r != "core" and not r.startswith("__")
        }
        assert not third_party, f"{path.name}: {sorted(third_party)}"


def test_no_platform_branching_in_ml() -> None:
    """core/ml がプラットフォーム名で分岐していないこと（設計破綻のサイン）。"""
    offenders: list[str] = []
    for path in _module_files():
        if _subpackage(path) != "ml":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            source = ast.dump(node.test).lower()
            if any(p in source for p in ("vertex", "sagemaker", "azureml", "snowflake")):
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, f"プラットフォーム分岐: {offenders}"


# --- platforms/ の階層規約（2026-08-01 追加）------------------------------
#
# `src/platforms/` 直下の判別基準は「**これは基盤か？**」の一問だけにする。
# 横断モジュールが直下に散ると、新しいファイルを置くたびに
# 「基盤なのか共通なのか」を目視で判断することになり、必ず崩れる。

PLATFORMS = REPO_ROOT / "src" / "platforms"

PLATFORM_PACKAGES = {"vertex", "sagemaker", "azureml", "databricks", "snowflake"}
# neon は比較対象ではない（計測の到達点）が、独立した層として直下に置く。
NON_PLATFORM_PACKAGES = {"neon", "shared"}


def test_platforms_top_level_has_no_loose_modules() -> None:
    """直下に置いてよい .py は `__init__.py` だけ。"""
    loose = {p.name for p in PLATFORMS.glob("*.py")} - {"__init__.py"}

    assert not loose, (
        f"src/platforms/ 直下に横断モジュールがある: {sorted(loose)}。"
        f"基盤でないものは shared/ へ置く"
    )


def test_platforms_top_level_is_exactly_platforms_plus_shared() -> None:
    """直下のパッケージが「5基盤 + neon + shared」に一致すること。"""
    packages = {p.name for p in PLATFORMS.iterdir() if p.is_dir() and not p.name.startswith("__")}

    assert packages == PLATFORM_PACKAGES | NON_PLATFORM_PACKAGES, (
        f"想定外のパッケージ: {sorted(packages ^ (PLATFORM_PACKAGES | NON_PLATFORM_PACKAGES))}"
    )


def test_adapters_do_not_import_each_other() -> None:
    """基盤 adapter どうしが依存しないこと（差が滲むと比較が成立しない）。"""
    for package in PLATFORM_PACKAGES:
        others = PLATFORM_PACKAGES - {package}
        for path in (PLATFORMS / package).rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            imported = {
                node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
            } | {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            }
            for other in others:
                assert not any(m.startswith(f"platforms.{other}") for m in imported), (
                    f"{path.relative_to(REPO_ROOT)} が platforms.{other} を import している"
                )
