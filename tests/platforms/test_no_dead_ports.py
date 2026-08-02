"""**実装したのに配線しない（死蔵）** を機械的に検出する。

このリポは §2.3 で「`ArtifactStore` を実装したのに実行経路から呼ばれていない」ことを
port を切ったのに配線しなかった欠陥として自己批判している。にもかかわらず
2026-08-02、AWS / Azure のパイプライン定義を**呼び出し元ゼロのまま実装し、
「実装済み」と報告した**（Vertex のパイプラインを撤去した理由と同じ罪）。

人の注意力では再発する。**「機能モジュールには実行経路からの呼び出し元がある」**を
テストで守る。ここが落ちたら、そのコードは動かせない = まだ実装が終わっていない。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.conftest import REPO_ROOT

SRC = REPO_ROOT / "src"
# 実行経路（ここから辿れないコードは誰も動かせない）
ENTRY_POINTS = sorted((REPO_ROOT / "scripts").glob("*.py")) + [REPO_ROOT / "Makefile"]

# 「機能」モジュール = 実行経路から呼ばれて初めて意味を持つもの。
# 契約・スキーマ・設定は他モジュールから import されるので対象外。
FEATURE_MODULES = (
    "platforms/sagemaker/pipeline.py",
    "platforms/azureml/pipeline.py",
    "platforms/vertex/experiment_observer.py",
    "platforms/shared/artifacts.py",
    "platforms/shared/resume.py",
)


def imported_modules(path: Path) -> set[str]:
    """`path` が import しているモジュール名（**AST のみ**。docstring は数えない）。

    文字列 grep だと「Vertex では見送った」等の**説明文**まで呼び出しに数えてしまい、
    番人が発火しなくなる（実際 2026-08-02 に一度そうなった）。
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def all_imports() -> set[str]:
    """実行経路（scripts）と src 全体が import しているモジュールの集合。"""
    found: set[str] = set()
    for path in [*sorted((REPO_ROOT / "scripts").glob("*.py")), *SRC.rglob("*.py")]:
        found |= {(module, path) for module in imported_modules(path)}  # type: ignore[misc]
    return found  # type: ignore[return-value]


@pytest.mark.parametrize("relative", FEATURE_MODULES)
def test_feature_module_has_a_caller(relative: str) -> None:
    """機能モジュールが**自分以外から** import されていること。

    落ちたら「実装したが誰も呼べない」状態 = 死蔵。**その状態を『実装済み』と
    呼ばない**（2026-08-02 に実際にやった）。
    """
    module_path = SRC / relative
    assert module_path.exists(), f"{relative} が無い"

    dotted = relative.removesuffix(".py").replace("/", ".")
    callers = [
        path
        for path in [*sorted((REPO_ROOT / "scripts").glob("*.py")), *SRC.rglob("*.py")]
        if path != module_path and dotted in imported_modules(path)
    ]

    assert callers, (
        f"{relative} を import するコードが実行経路に無い（死蔵）。"
        "スクリプト / adapter から配線するか、実装ごと撤去する"
    )


def test_pipeline_entry_point_exists_and_covers_supported_platforms() -> None:
    """パイプラインを実際に叩ける口があること。

    定義を組めるだけでは「対応した」と言えない —— 2026-08-02 の失敗そのもの。
    """
    import sys

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from tests.conftest import load_script

    run_pipeline = load_script("run_pipeline")

    assert set(run_pipeline.SUPPORTED) == {"sagemaker", "azureml"}
    # Vertex は器が無く見送り（P3）。黙って足さない
    assert "vertex" not in run_pipeline.SUPPORTED


def test_makefile_exposes_the_pipeline_targets() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "pipeline-build:" in makefile
    assert "pipeline-submit:" in makefile
