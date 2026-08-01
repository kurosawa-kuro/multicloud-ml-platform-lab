"""Loader for the recorded 5-platform train runs.

The comparison claim ("same data + same training code across five platforms
yields the same metrics") is backed by rows that live in Neon. Tests must run
without cloud credentials (`make test` is credential-free), so the evidence is
committed as a snapshot under `tests/data/` and read from here.

Regenerate the snapshot only from Neon, never by hand:

    doppler run -- .venv/bin/python -m tests.platform_runs --refresh

Hand-editing the snapshot would turn the parity gate into a tautology.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = Path(__file__).resolve().parent / "data" / "platform_train_runs.json"

# The five platforms the lab compares. A missing one must fail loudly rather
# than silently shrink the comparison.
EXPECTED_PLATFORMS = frozenset({"vertex", "sagemaker", "azureml", "databricks", "snowflake"})

# Training logic lives here. Platform adapters and docs may differ between the
# recorded commits; this subtree may not.
TRAINING_SUBTREE = "src/core/ml"

# 2026-08-02: 公開のため git 履歴を作り直した（`rm -rf .git && git init`）。下の5コミットは
# その旧履歴にあり、**このリポジトリからは二度と解決できない**。
#
# 失われたのは「5基盤が同一の学習コードで動いた」ことを *再* 検証する手段であって、
# 検証した事実そのものではない。リセット前に実測済みで、`src/core/ml` の tree hash は
# 5つとも `a1b73934` で一致していた（test_code_revision_parity.py の docstring に記録）。
# ただし **その値をここから再導出することはできない**ので、定数として突き合わせても
# 同語反復にしかならない。だから tree 一致の検証は「解決できる run」に対してだけ行う。
#
# 免除を SHA の名指しにしているのは、これを「解決できないものは全部飛ばす」にすると
# **新しい run が埋め込み漏れや別リポ由来の SHA を持っていても黙って通る**ため。
# ここに載っていない未解決 SHA は落とす。
PRE_HISTORY_RESET_REVISIONS: frozenset[str] = frozenset(
    {
        "ddb1f092795a5200d82639f4a87f59861f25ff39",  # azureml
        "4129907cae5b4add86367d950b8415be4370552a",  # databricks
        "7e6dc1c00f96dd716d82299981c017900e29df5a",  # sagemaker
        "e4eeaab5658a23640d8118d0b6a39292ebb66712",  # snowflake
        "35d48cbbb92e8700f78ea54df8fc9495d4d7fff2",  # vertex
    }
)


@dataclass(frozen=True)
class PlatformRun:
    platform: str
    tier: str
    code_revision: str
    write_path: str
    rmse: float
    mae: float
    r2: float


def load_runs(snapshot: Path | None = None) -> list[PlatformRun]:
    """Read the committed snapshot of successful train runs (one per platform)."""
    path = snapshot or SNAPSHOT
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [PlatformRun(**entry) for entry in payload["runs"]]


def git_tree_hash(revision: str, subtree: str = TRAINING_SUBTREE) -> str:
    """Tree hash of `subtree` at `revision`.

    Two commits with different SHAs can still carry byte-identical training
    code. That is exactly what happened during the 2026-08-01 runs, so the
    tree hash — not the commit SHA — is what makes the comparison valid.
    """
    return subprocess.run(
        ["git", "rev-parse", f"{revision}:{subtree}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def commit_exists(revision: str) -> bool:
    """Whether `revision` resolves to a commit object in this repository."""
    return (
        subprocess.run(
            ["git", "cat-file", "-e", f"{revision}^{{commit}}"],
            cwd=REPO_ROOT,
            capture_output=True,
        ).returncode
        == 0
    )


def refresh_snapshot(captured_at: str, snapshot: Path | None = None) -> Path:
    """Rewrite the snapshot from Neon. Requires NEON_MULTICLOUD_POOLED_URI.

    Keeps the first successful train run per platform — later re-runs of the
    same platform do not change what the comparison report was built on.
    """
    import os  # noqa: PLC0415 - only needed when refreshing

    import psycopg  # noqa: PLC0415 - not a test-time dependency

    query = """
        select platform, tier, code_revision, write_path,
               (metrics->>'rmse')::float8, (metrics->>'mae')::float8, (metrics->>'r2')::float8
        from ml_runs
        where stage = 'train' and status = 'success' and metrics ? 'rmse'
        order by platform, created_at
    """
    with psycopg.connect(os.environ["NEON_MULTICLOUD_POOLED_URI"]) as connection:
        rows = list(connection.execute(query))

    seen: set[str] = set()
    runs: list[dict[str, object]] = []
    for platform, tier, revision, write_path, rmse, mae, r2 in rows:
        if platform in seen:
            continue
        seen.add(platform)
        runs.append(
            {
                "platform": platform,
                "tier": tier,
                "code_revision": revision,
                "write_path": write_path,
                "rmse": rmse,
                "mae": mae,
                "r2": r2,
            }
        )

    path = snapshot or SNAPSHOT
    payload = {
        "captured_at": captured_at,
        "source": "Neon ml_runs (stage=train, status=success)",
        "runs": runs,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":  # pragma: no cover - operator entry point
    import argparse

    parser = argparse.ArgumentParser(prog="tests.platform_runs")
    parser.add_argument("--refresh", action="store_true", help="rewrite the snapshot from Neon")
    parser.add_argument("--captured-at", required=False, default="", help="YYYY-MM-DD")
    args = parser.parse_args()
    if not args.refresh:
        parser.error("--refresh is the only supported action")
    if not args.captured_at:
        parser.error("--captured-at YYYY-MM-DD is required (no clock access in tests)")
    print(f"wrote {refresh_snapshot(args.captured_at)}")
