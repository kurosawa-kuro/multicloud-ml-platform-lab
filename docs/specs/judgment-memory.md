# Judgment Memory（このプロジェクトの instantiation）

**Layer 9: Judgment Memory**（§17）の repo 固有 instantiation。Skills: `log-decision`（収集）→ `distill-memory`（レビュー/昇格/却下）。

## パイプライン

```text
Decision Event   docs/decisions/decision-log.md      生の判断日誌（append-only、log-decision）
  → Candidate    docs/memory/memory-candidates.md    再発 or 高損失、証拠待ち
    → Distilled  docs/memory/distilled-memory.md     再利用可能な判断原則（runtime で読む唯一の層）
      → Rule/Hook/Skill                               機械的に検出可能 かつ 高損失 の時のみ
  → Rejected     docs/memory/rejected-memory.md       一回限り / 文脈依存 / test-CI-permissions で既出
```

## いつ何を読むか

- `docs/decisions/decision-log.md` — レビュー専用、毎セッションはロードしない（肥大化回避、§22.8）。
- `docs/memory/distilled-memory.md` — 蒸留済み原則。runtime 再利用を意図する唯一の memory 層。
- 蒸留（`distill-memory`）は定期 / incident 後に行う。常時ルールにはしない。

## 昇格 vs 却下

昇格: 2〜3回再発した / 一度が高損失だった / owner-only ラインを越えた / 保護 capability・path に触れた。
機械的に検出可能で常時ルールが安価な時だけ `rule`/`hook` 化を提案する（例: 新しい保護パス → `detect-safety-boundary` に追加）。

却下/失効: 一回限り / 強い文脈依存 / 探索を鈍らせるだけ / test・CI・permissions で既に捕捉済み。

## global `~/.claude` memory との関係

- **このリポジトリの `docs/memory/`** = このプロジェクトの判断*原則*（どう賭け/止め/scope を切るか）。コードと一緒にコミット・レビューする。
- **global `~/.claude/.../memory/`** = プロジェクト横断の事実（operator・インフラ構成・preferences）。
- 横断に一般化した原則は global へ昇格し、repo memory は repo スコープに保つ。
- どちらも secret/token/個人 payload を書かない — 判断の*構造*だけ残す（`.claude/rules/security.md`）。
