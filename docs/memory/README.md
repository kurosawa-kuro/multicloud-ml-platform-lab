# docs/memory — Judgment Memory store

このリポジトリの **Layer 9** 蒸留済み判断記憶。方針とパイプライン: `docs/specs/judgment-memory.md`。Skill: `distill-memory`（ここへ書く）、`log-decision`（`docs/decisions/decision-log.md` へ書く）が供給。

| File | 役割 | runtime で読む? |
|---|---|---|
| `distilled-memory.md` | 再利用可能な判断原則（抽象・少数） | **yes** — 再利用でロードする唯一の memory 層 |
| `memory-candidates.md` | 再発/高損失で証拠待ちの判断 | no — レビュー専用 |
| `rejected-memory.md` | 一回限り/文脈依存/既出、理由つき | no — レビュー専用 |

生の判断イベントは `docs/decisions/decision-log.md`（ここではない）。このストアは*蒸留済み*出力だけを持つ。

global `~/.claude` memory とは別物: あちらはプロジェクト横断の operator/インフラ事実、ここはこのプロジェクト固有の判断原則。横断に一般化する原則は global へ。
