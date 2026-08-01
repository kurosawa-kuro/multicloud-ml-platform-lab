# `.claude` — このリポジトリのハーネス

AI エージェント（Claude Code）を、このリポジトリで**安全に・止まりすぎずに**動かすための制御一式（ハーネス）。
人間向けの「各ファイルが何の責務を持つか」の説明はこの README に集約する。各ファイル内のコメントは補助。

このハーネスは **Kurosawa Thin Harness Architecture**（正本: `docs/specs/kurosawa-thin-harness-architecture.md`、tool-agnostic）の **Claude Code 向け integration** である。アーキ本体（仕様・テンプレ・判断記憶）は `docs/` に置き、`.claude/` はそれを呼ぶ薄い adapter に保つ。

> **このハーネスは意図的に「重い既定」で出荷されている。** generator（テンプレ）側は hooks・rules・agents・skills・permissions を厚く盛り、コピー先で**削って初期調整する**のが規約（richness の流れは template → 生成先）。生成直後にまず skill **`harness-trim`** を1回回し、このプロジェクトの脅威モデルに合わせて keep / delete / adjust を決めること。使わない toolchain の permissions 行、不要な hook（1 hook = 1 ファイル + settings 1 ブロックで削除可能）、対象外言語の rule は消してよい——削除は scope creep ではなく想定手順。

## 公式リファレンス（Claude Code）

このハーネスの「幹」は Claude Code 公式機能。各層が依拠する一次情報（迷ったらまずここを引く）:

| 公式ドキュメント | URL | このハーネスでの対応 |
|---|---|---|
| Overview / 索引 | https://code.claude.com/docs | 全体の入口 |
| Best practices | https://code.claude.com/docs/en/best-practices | Explore→Plan→Implement→Commit、SPEC.md、plan モード、TDD |
| Memory（CLAUDE.md） | https://code.claude.com/docs/en/memory | 自動ロード: `CLAUDE.md`/`CLAUDE.local.md`/`.claude/rules/*.md`、`@path` import、AGENTS.md の扱い |
| Skills | https://code.claude.com/docs/en/skills | `.claude/skills/<名>/SKILL.md`（frontmatter `name`/`description`） |
| Subagents | https://code.claude.com/docs/en/sub-agents | `.claude/agents/<名>.md`（隔離コンテキスト、`explore`/`verify`） |
| Slash commands | https://code.claude.com/docs/en/commands | `/init` `/plan` ほか。旧 `.claude/commands/*.md` は skills へ統合 |

**幹と枝葉**: 公式が認識する層（CLAUDE.md / skills / agents / rules）は公式仕様どおりに保つ。その上の中身（docs 章立て・tasks 書式・Makefile 名など）はプロジェクト固有でよい。`SPEC.md` は自動ロードされない命名規約で、CLAUDE.md から参照して初めて効く。`AGENTS.md` は Codex 等他ツール用のクロスツール標準で Claude Code 非ネイティブ（併存させる）。

## 設計思想 — 「何を守るか」から決める

ハーネスはテンプレではなく、このプロジェクトで**壊れたら困るもの（脅威モデル）**の写像。
**このプロジェクトで守る対象を最初に言語化すること**（例: secret / 外部副作用・不可逆操作 / 正本データの整合 / 本番データ）。それが決まると、permissions の ask/deny と `detect-safety-boundary` の protected path をどこに引くかが決まる。

> このテンプレは**安全既定（一般則）**で出荷されている: 生インフラ/DBツール（`gcloud`/`terraform`/`psql`/`aws`/`turso`/`supabase`）は **ask**、不可逆なコード/履歴破壊（`rm -rf`/force-push/`git reset --hard`）だけ **deny**。プロジェクトの脅威モデルが「壊れても再構築できる・loss-critical 資産が無い」なら raw ツールを allow に緩めてよいし、本番データを扱うなら ask/deny を厚くする。**他プロジェクトの permissions をそのまま移植しない**——重心は守る対象で変わる。

## 10 Layer → このリポジトリでの実装

アーキ文書の Layer 0–9 を skill / rule / hook / setting / spec へ写像したもの。常時ロード面（CLAUDE.md + rules）は薄いまま、重量はオンデマンドな skill / template / spec / hook に寄せる。

| Layer | 文書の名称 | 実装 | 強制力 |
|---:|---|---|---|
| 0 | Thin Harness | この README + メンテ方針 | 思想 |
| 1 | Harness Weight Class | skill `classify-task` + CLAUDE.md Runtime Protocol | ソフト |
| 2 | Task / Feature Contract | skill `create-task`（台帳・Lite/Full）+ `docs/templates/task-contract-*.md` ／ **`SPEC.md`**（いま作る1機能の深い実装スペック・公式入口、触るファイル名指し＋E2E検証） | ソフト |
| 3 | Human Judgment Gate | skill `scan-decisions` + `docs/specs/runtime-protocol.md`（stop rules） | ソフト |
| 4 | Capability Boundary | `settings.json` permissions + skill `assess-risk` + `docs/specs/capability-boundary.md` | **ハード**＋ソフト |
| 5 | Change Boundary | skill `control-change` + **hook `detect-safety-boundary`** + `docs/specs/change-boundary.md` | **ハード**＋ソフト |
| 6 | Skeleton / TDD | skill `plan-skeleton` + `execute-task` | ソフト |
| 7 | Reconcile Controller | skill `reconcile-task` + **hook `detect-scope-creep`** | ソフト＋nudge |
| 8 | Evidence / Reality Check | skill `verify-completion`（Evidence Level 0–4）+ `check-claims` + **hook `detect-unverified-claim`** + `docs/specs/evidence-policy.md` | ソフト＋nudge |
| 9 | Judgment Memory | skill `log-decision`（収集）→ `distill-memory`（蒸留）+ `docs/decisions/` + `docs/memory/` + `docs/specs/judgment-memory.md` | 観測 |

> 「**ソフト＝AI への指示（無視されうる）／ハード＝ツール層で実際に止まる**」。permissions（Bash を止める）と `detect-safety-boundary`（Edit/Write の対象パスを止める）の2つでハードを多重化する。

## コンポーネント責務一覧

| 場所 | 責務 | 強制力 | 読み込み |
|---|---|---|---|
| `../CLAUDE.md` | 司令塔。最小の判断基準＋薄い Runtime Protocol。詳細は `docs/` を指す | ソフト | 常時 |
| `rules/` | パス別の柔らかい制約（docs 整合・secret 混入防止 等） | ソフト | 該当パス編集時 |
| `settings.json` (`permissions`) | 操作の許可/承認/禁止を**物理的に**強制 | ハード | 常時 |
| `settings.json` (`hooks`) + `hooks/` | 検出 hooks・自動整形・セッション文脈注入/ログの軽量自動処理 | ハード/nudge | イベント時 |
| `settings.json` (`statusLine`) + `statusline.sh` | プロジェクト名・branch・active task 数・model を1行表示（装飾。不要なら両方削除） | — | 常時 |
| `../.mcp.json` | project-scoped MCP server の置き場（既定は空 stub）。秘密を持つ server は `settings.local.json` へ | — | 常時 |
| `rules/` の言語別（`rust`/`python`/`terraform`/`scripts`/`tests`） | 対象パス編集時だけ効くスタック規約。使わない言語の rule は削除 | ソフト | 該当パス編集時 |
| `skills/` | 頻出・低リスクな作業手順の呼び出し（Layer 1–9 の手続き本体、main loop で実行） | 補助 | 呼ばれた時のみ |
| `agents/` | 隔離コンテキストの subagent（5）。`explore`＝read-only 探索、`plan`＝実装計画、`verify`＝主張の反証、`review-diff`＝変更品質レビュー、`security-review`＝安全観点レビュー。skill が main loop なのに対し agent は別文脈 | 補助 | 呼ばれた時のみ |
| `logs/` | セッション実行ログ・scope カウンタ（gitignore 済み） | — | — |
| `../docs/specs/` | アーキ本体（マスター）＋ repo 固有 instantiation | — | 参照時 |
| `../docs/templates/` | 各 Layer のテンプレ | — | 必要時 |
| `../docs/decisions/decision-log.md` | 判断日誌。append-only、`log-decision` が追記、Stop hook が boundary 挿入 | — | レビュー時 |
| `../docs/memory/` | 蒸留済み判断記憶（`distill-memory` が更新） | — | 参照時 |

## settings.json — 強制層（Layer 4 の本丸）

`permissions` は3分類。安全既定（一般則・厚めのメニュー）で出荷し、コピー先で使わない行を削る:

- **allow**: 安全な開発ループ（`make *`、`cargo *`、`uv run`/`pytest`/`ruff`/`mypy`、`npm test/lint`、`go test`、read-only git ＋ `git add`/`git restore`、`rg`/`ls`/`cat`/`grep`/`find`/`jq`、**標準ランナー `doppler run`** 等）→ 確認なしで進む
- **ask**: `git commit`/`push`/`reset`/`rebase`/`checkout`、生インフラ/DB/クラウド（`gcloud`/`gsutil`/`bq`/`aws`/`gh`/`terraform`/`docker`/`kubectl`/**生** `psql`/`turso`/`supabase`）・secret **書き込み**（`doppler secrets set` 等）・`curl`/`wget`・publish・`Read(env/secret*.yaml)`→ 都度承認。**`doppler run` は ask に置かない**（非対話/auto では ask=classifier hard-block で標準ランナーが全滅した系統バグ。injection 自体は無害で、危険は secret write と実行先コマンド側で governed）
- **deny**: 不可逆な**コード/履歴破壊**（`rm -rf`、`sudo`、force-push、`git reset --hard`、`git clean -fdx`、`terraform destroy`）＋ `Read(.env*)`→ 実行不可

> 個人開発は main 直コミット運用のため `git commit` は **ask**（deny ではない）に置き、`git push` を別枠 ask にしている。共有 repo に配るときは commit ポリシーを見直す。

### hooks

| hook | イベント | 役割 | 強制力 |
|---|---|---|---|
| `detect-safety-boundary.sh` | PreToolUse `Edit\|Write\|MultiEdit` | protected **path**（`env/secret/**`/`infra/**`/`terraform/**`/`.github/workflows/**`）の編集を exit 2 で止め owner 確認へ。**permissions が見ない Edit/Write の隙間埋め** | ハード |
| `detect-secret-content.sh` | PreToolUse `Edit\|Write\|MultiEdit` | 書き込み**内容**が secret 形状（AWS/GitHub/Slack/Google/Doppler token・private key・bearer）なら exit 2。path 側と対の**内容側**ガード | ハード |
| `detect-secret-read.sh` | PreToolUse `Bash` | secret を読む Bash（`cat env/secret.yaml`・`.env`・`$DOPPLER_TOKEN` 等）を exit 2 で止める。allow した read ツールの**読み取り側**の穴埋め | ハード |
| `detect-scope-creep.sh` | PostToolUse `Edit\|Write\|MultiEdit` | 1 セッションの編集ファイル数が Standard 上限(8)超で非ブロッキング advisory | nudge |
| `format-on-edit.sh` | PostToolUse `Edit\|Write\|MultiEdit` | 編集した1ファイルを、対応 formatter がある時だけ自動整形（無ければ無音 no-op）。安全制御ではない自動化——邪魔なら真っ先に削除可 | 自動化 |
| `session-start-context.sh` | SessionStart | `docs/tasks/03_active/`＋`04_verifying/` 一覧＋`distilled-memory` の要点を additionalContext で注入し Layer 9 を毎セッション還流 | nudge |
| `session-end.sh` | Stop | セッション終了時刻を log に、decision-log に session boundary を冪等追記（連続空 boundary を作らない） | — |
| `detect-unverified-claim.sh` | Stop | `docs/tasks/05_done/` 変更時に「done は Evidence Level ≥2」を reminder | nudge |

protected path はプロジェクトに合わせて `hooks/detect-safety-boundary.sh` と `docs/specs/change-boundary.md` で調整する。secret 形状パターンは `detect-secret-content.sh`/`detect-secret-read.sh` に、扱う credential 形に合わせて足し引きする。

## skills/ — 作業手順（オンデマンド、常時トークンを食わない）

```
生成直後: harness-trim（重い既定を脅威モデルへ右サイズ化。1回だけ）
通常:      classify-task ─→ create-task ─→ scan-decisions ─→（Standard/Heavy 機能は SPEC.md を記述）─→ plan-skeleton ─→ execute-task/reconcile-task ─→ check ─→ verify-completion ─→ commit ─→ review-task
危険作業:    ↑           ─→ scan-decisions ─→ assess-risk ─→ ...（Heavy は Task Contract Full + owner approval）
割り込み:   実行中に scope 外/前提崩れ/新リスク/Weight Class 上昇を見つけたら control-change
横断（収集）: 実際に判断を下した時は log-decision で1行残す
横断（蒸留）: decision-log が溜まったら distill-memory で memory-candidates → distilled-memory へ昇格 / rejected へ
```

全部を毎回必須にしない（重くなる）。**Light タスクは classify-task で分類した後、重い手順（scan-decisions 一括質問・plan-skeleton・reconcile-task）をスキップしてよい**——これが Thin Harness を能動的に効かせる仕組み（文書 §22.2: gate が強すぎると速度が死ぬ）。

| Skill | Layer | 責務 |
|---|---|---|
| `harness-trim` | 0 | **生成直後に1回**。重い既定のハーネスを、このプロジェクトの脅威モデルに合わせ keep/delete/adjust する想定手順。閾値が変わった時も再実行 |
| `classify-task` | 1 | Light/Standard/Heavy を判定し default limits を出す。重さの起点 |
| `create-task` | 2 | docs/tasks に作業メモ（台帳）を1枚（Lite/Full の Task Contract）。深い機能スペックは `SPEC.md` に書き、task note はそこへリンクして Goal/Scope を二重化しない |
| `scan-decisions` | 3 | 着手前に「人間の判断が要る箇所」を洗い出し、真の blocker だけ一括質問・残りは既定値で進める |
| `assess-risk` | 4 | 危険作業の前に「失敗したら何が壊れるか」を評価 |
| `control-change` | 5 | 作業中に見つけた scope 外変更/前提崩れ/新リスクを分類 |
| `plan-skeleton` | 6 | 本実装前に構造（files/interface/stub）と TDD contract を固定 |
| `execute-task` | 6 | メモ通りに、既存挙動・テスト・docs を壊さず小さく実装する |
| `reconcile-task` | 7 | 複数 attempt を停止条件付きループで回す。scope を広げて継続しない |
| `check` | 8 | プロジェクトの品質ゲート（fmt/lint/test）を実際に走らせ、実出力で pass/fail を報告。verify-completion の機械的な下限 |
| `verify-completion` | 8 | Goal/Acceptance を**証拠付き**で満たしたか判定。Evidence Level ≥2 が done の下限 |
| `check-claims` | 8 | 結論を書く前に実ファイル/コマンドで主張を裏取り |
| `commit` | 8 | check + verify 済みの変更を、secret 混入なし・docs 追随ありで確認してからコミット。push は別枠 owner 承認 |
| `log-decision` | 9 | 実際に下した判断1件を decision-log へ append |
| `distill-memory` | 9 | decision-log を蒸留し distilled-memory へ昇格・一回限りを rejected へ |
| `review-task` | 終結 | メモ自体の品質を点検する |
| `review-project` | 終結 | 構造・docs・責務境界・テスト不足をレビューする |
| `plan-refactor` | 補助 | 重複/責務ズレの整理計画を、早すぎる共通化なしに立てる |
| `distill-spec` | 補助 | 生ブレスト/貼り付けメモを 01_requirements(仕様)/02_architecture(基礎設計) へ蒸留分離する |

## メンテ方針

- **thin harness**: 常時ロードされる `CLAUDE.md` と `rules/` は最小に保つ。詳細仕様は `docs/specs/`、テンプレは `docs/templates/`、人間向け説明はこの README に集約する。10 Layer 全採用でも、追加重量はオンデマンド面に置く。
- skills は呼ばれた時しか読まれないので増やしても常時コストは低い。汎用 skill は陳腐化に注意。
- 守る対象（secret・不可逆操作・正本・本番データ）が変わったら、まず `settings.json` の ask/deny と `detect-safety-boundary` の protected path、`detect-secret-content`/`detect-secret-read` の credential 形状を見直す。
- **重い出荷 → trim が前提**: このハーネスは厚く出荷される。生成直後に `harness-trim` で使わない面（permissions 行・hook・言語 rule）を削るまでが1セット。削らず全部抱えたまま全タスクが重く感じるなら §22 の破綻兆候——trim を強める。
- **破綻監視（文書 §22）**: Weight Class が形骸化し全タスク Heavy 扱いになる / Judgment Memory が生ログのまま肥大化する / hooks が誤検知で速度を削ぐ、のいずれかが起きたら畳む。
- 形骸化した層は撤去してよい（decision-log が雑用ログで薄まったら Stop hook の boundary 追記を外す、hook が邪魔なら `settings.json` の該当エントリ削除）。
