"""Derive the wheel filename from pyproject metadata.

Tier B ships a wheel (Databricks) built by `python -m build`. Three places used
to spell that filename out, version included:

    env/config.yaml                       wheel_filename: ...-0.1.0-...whl
    src/platforms/databricks/adapter.py   DatabricksConfig.wheel_filename default
    infra/modules/databricks/main.tf      local.wheel_path default

Bumping `[project].version` while missing one of them breaks the job at run time
with `python_wheel_task` failing to resolve its entry point — a failure that only
shows up in the cloud. pyproject is the single source; everything else derives.

`infra/**` cannot import Python, so the Terraform default stays written out, and
`tests/test_packaging_contract.py` pins it against this function instead.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PYPROJECT = REPO_ROOT / "pyproject.toml"

# PEP 427: distribution names are normalized with '-' and '.' collapsed to '_'.
_NON_ALPHANUM = re.compile(r"[-_.]+")


def normalize_distribution(name: str) -> str:
    return _NON_ALPHANUM.sub("_", name)


def project_metadata(pyproject: Path | None = None) -> tuple[str, str]:
    """`(name, version)` from `[project]`."""
    path = pyproject or PYPROJECT
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    project = data["project"]
    return project["name"], project["version"]


def wheel_filename(pyproject: Path | None = None) -> str:
    """The wheel `python -m build` produces for this project."""
    name, version = project_metadata(pyproject)
    return f"{normalize_distribution(name)}-{version}-py3-none-any.whl"
