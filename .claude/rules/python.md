---
paths:
  - "**/*.py"
  - "**/pyproject.toml"
---

# Python ルール

- 依存とタスク実行は `uv` に寄せる（`uv sync` / `uv run`）。グローバル pip は使わない。
- Lint / format は `ruff`（`ruff check` / `ruff format`）、型は `mypy`。挙動を変えたら実際に走らせて確認する。
- ML 応用 API は FastAPI を第一候補にする。通常の非 ML API は Rust + axum 側に寄せる。
- ライブラリのバージョンは、学習時と serving/本番コンテナで**同一 minor に固定**する（`>=X.Y,<X.(Y+1)`）。silent な互換ズレを作らない。
- テストは `pytest`。副作用（ネットワーク・課金 API・本番 DB）は fixture でモックし、実接続テストは明示マークで隔離する。
