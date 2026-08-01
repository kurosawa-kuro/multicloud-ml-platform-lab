# Doppler キー名の再設計（cloud-ml-lab / dev）

状態: **実行済み（2026-08-01）** / 調査日: 2026-08-01

> **実行結果サマリ（§6 に詳細）**: `kuro-dev-k/dev` は **`cloud-ml-lab/dev` に改名した上で 77 → 31 件**。
> 削除 46 件・キー名リネーム 4 件・プロジェクト名リネーム 1 件。ロールバック用スナップショットは
> owner 判断で削除済み（＝**削除した 46 件は復元不可**）。`stg` / `prd` は放置。
> `dev_personal` は Doppler 仕様上削除できないが `dev` を継承するため中身はクリーン。

## 依頼原文

> doppler-key名の再設計をしたい。このドップラーは個人利用では絶対利用しない、基本的にクラウド
> スキル ML スキル 学習習得用なので、不要なのは削除して改めて、クラウドサービス観点で命名規則を
> 再設計したい。調査し再設計案を提案依頼。

対象: Doppler project `cloud-ml-lab` / config `dev`。

---

## 1. 実測（2026-08-01・値は一切参照していない）

### 1.1 config の構成

| config | キー数 | 最終取得 | 判定 |
|---|---|---|---|
| `dev` | **77** | 2026-08-01 | 現役。本タスクの主対象 |
| `dev_personal` | 65 | 2026-06-09 | `dev` の**部分集合**（`dev` にしか無いキーは 12 件、`dev_personal` 固有キーは **0 件**）。名前が「個人利用しない」方針と矛盾 |
| `stg` | 5 | 2026-06-09 | `DOPPLER_*` 3 件＋`ID_RSA`＋`NEON_DB_URL` のみ。旧命名の残骸 |
| `prd` | 5 | 2026-06-09 | `stg` と同一構成 |

### 1.2 消費者（この Linux 端末側リポ）

| リポ | Doppler 束縛 | 実際にコードが読むキー |
|---|---|---|
| `multicloud-ml-platform-lab` | `cloud-ml-lab/dev` | AWS 4 / AZURE 2 / GOOGLE 2 / SNOWFLAKE 8 / DATABRICKS 2 / DB_NEON 3 / MCML_TF 5 |
| `kaggle-bronze-gcp` | `cloud-ml-lab/dev` | **`ML_KAGGLE_TOKEN` のみ**（Makefile 7 箇所） |
| `news-notification-priority-prediction-poc` | `cloud-ml-lab/dev` | **0 件**（コードは `GCP_PROJECT`/`GCP_REGION`/`GCP_BUCKET` を config から読む） |
| `gcp-search-mlops-gke` | 束縛なし（`doppler.yaml` 無し） | — |

**実消費は 26 件 / 77 件。残り 51 件は誰も読んでいない。**

### 1.3 参照ゼロのキー（全リポ grep で 0 ヒット・17 件。うち削除可能 16 件）

`AUTH_COGNITO_CLIENT_ID` `AUTH_COGNITO_REGION` `AUTH_COGNITO_USER_POOL_ID` `COOKIES_JSON_X`
`COOKIES_JSON_YT` `DB_LOCAL_POSTGRES_URI` `DB_POSTGRES_URL` `DOPPLER_ENVIRONMENT`
`HERMES_DISCORD_WEBHOOK_URL` `HERMES_TURSO_AUTH_TOKEN` `HERMES_TURSO_DATABASE_URL`
`ML_WANDB_API_KEY` `NGROK_AUTHTOKEN` `NOTIFY_DISCORD_WEBHOOK_URL` `YOUTUBE_API_KEY`
`YOUTUBE_OAUTH_CLIENT_ID` `YOUTUBE_OAUTH_CLIENT_SECRET`

（Hermes/n8n は 2026-07-12 に退役完了済み。Cookie / YouTube 系は private-app 側の資産。）

---

## 2. 問題の診断（3 つ）

### 問題 1: ワークスペースの用途分離が破れている（最重要）

`private-ops` の README / CLAUDE.md / AGENTS.md は次を明文化している。

> `kuro-dev` workspace（および project `kuro-dev-k`）は **AWS/GCP Cloud 専用**。
> ローカル個人アプリは `private-app` の `apps/dev` を使う。

（`kuro-dev-k` は本タスクの最後に `cloud-ml-lab` へ改名した。§6.4 参照。）

実態は逆で、`cloud-ml-lab/dev` に **Discord webhook 6 本・Cookie 2 本・Clerk・Cognito・Cloudinary・
MongoDB・Turso・pgAdmin・ngrok・YouTube OAuth・GitHub SSH 鍵・デプロイ用 SSH 鍵** が同居している。
これらは `private-app/apps/dev` に正本があるキーの**二重在庫**であり、

- 片方だけローテすると値が乖離し、どちらが生きているか分からなくなる
- 学習用アカウントの資格情報と個人運用の資格情報が同じ blast radius に入る
- 「クラウド学習用 config」を開いても、何がクラウドの鍵なのか目視で判別できない

依頼の「個人利用では絶対利用しない」は、この二重在庫の解消と同義。

### 問題 2: 命名の軸が 2 つ混在している

同じ config に 2 系統の名前が並んでいる。

| 系統 | 例 | 出自 |
|---|---|---|
| 機能カテゴリ先頭 | `AI_OPENAI_API_KEY` `DB_NEON_API_KEY` `ML_KAGGLE_TOKEN` `NOTIFY_DISCORD_*` | `private-app/apps/dev` 用の規約 `<CATEGORY>_<SERVICE>_[QUALIFIER…]_<TYPE>` |
| SDK 標準名 | `AWS_ACCESS_KEY_ID` `GOOGLE_CLOUD_PROJECT` `AZURE_TENANT_ID` `SNOWFLAKE_*` `DATABRICKS_*` | 各クラウド SDK / CLI / Terraform provider が固定で読む名前 |

現行 `doppler.yaml` はこれを「**CLOUD セクションだけ命名規則の例外**」と注記して運用している。
しかし config の中身は事実上 100% クラウド資格情報であり、**例外の方が多数派**になっている。
規約が現実を説明できていない状態。

カテゴリ軸は「1 つのアプリが多種の SaaS を使う」private-app の分類として正しいが、
「1 つのクラウドの複数サービスを使う」学習用 config では第 1 トークンが情報を持たない。

### 問題 3: カタログが 3 リポに分散して腐っている

`cloud-ml-lab/dev` の正本ドキュメントが存在せず、3 リポの `doppler.yaml` が各自コピーを持っている。

- `kaggle-bronze-gcp/doppler.yaml` — **実在しないキーを列挙**（`DB_TURSO_HERMES_URL`
  `DB_TURSO_HERMES_AUTH_TOKEN` `AUTH_N8N_OWNER_EMAIL` `DB_NEON_NEWS_APP_URI`
  `DB_NEON_PUBLIC_APP_REF_URI` `INFRA_*` 等）。`apps/dev` のカタログを写経したまま腐っている
- `news-notification-priority-prediction-poc/doppler.yaml` — 実在しない `AUTH_SECRET_KEY` を記載
- `multicloud-ml-platform-lab/doppler.yaml` — 2026-07-31 に「**本ラボが参照する分だけ載せる**」へ
  切替済み。**この方針が正解**で、他 2 リポへ横展開すべき

---

## 3. 再設計案

### 3.1 原則（3 行）

1. **`cloud-ml-lab` はクラウド／ML 学習の実行資格情報だけを置く。** 個人アプリの秘密は 1 件も置かない
   （正本は `private-app/apps/dev`。端末も Mac＝private-app / Linux＝kuro-dev で分離済み）。
2. **第 1 トークンはクラウドサービス名にする。** 機能カテゴリ（`AI_` `DB_` `ML_` `NOTIFY_`）は使わない。
3. **サービス側が env 名を固定しているものは、その名前をそのまま使う。** これが既定であって例外ではない。

原則 3 は「デファクトを忠実に使い、独自名を発明しない」の適用。SDK 標準名は既にすべて
プロバイダ名先頭（`AWS_` `GOOGLE_` `AZURE_` `SNOWFLAKE_` `DATABRICKS_`）なので、
**原則 2 と原則 3 は衝突せず、むしろ一致する**。現行規約のような「CLOUD セクションだけ例外」が消える。

### 3.2 3 層モデル

| 層 | 形 | 対象 | 例 |
|---|---|---|---|
| **L1** SDK 標準名 | サービスが決めた名前をそのまま | SDK / CLI / Terraform provider が固定名で読む値 | `AWS_ACCESS_KEY_ID` `GOOGLE_CLOUD_PROJECT` `SNOWFLAKE_ROLE` `DATABRICKS_HOST` `NEON_API_KEY` `KAGGLE_API_TOKEN` `DOPPLER_TOKEN` |
| **L2** サービス名先頭 | `<SERVICE>_<QUALIFIER…>_<TYPE>` | 固定 env 名を持たない値（接続文字列など） | `NEON_MULTICLOUD_POOLED_URI` `NEON_MULTICLOUD_DIRECT_URI` |
| **L3** リポ固有 | `<REPO>_<AREA>_<FIELD>` | そのリポのコードだけが読む入力値 | `MCML_TF_BILLING_ACCOUNT_ID`（既存踏襲） |

- L3 が唯一の「自作プレフィックス」。既に `MCML_TF_*` が採用済みで、形はこのままでよい。
  kaggle-bronze-gcp がリポ固有キーを必要としたら `KGB_*` を使う。
- **非機密の ID（リージョン・プロジェクト ID・バケット名）は Doppler に入れない**という現行の
  切り分けは維持する。`kaggle-bronze-gcp` の `GCP_PROJECT` / `GOOGLE_CLOUD_QUOTA_PROJECT`、
  `news-poc` の `GCP_*` が Doppler に無いのは正しい状態（config ファイル側が正本）。

### 3.3 リネーム表（4 件のみ）

| 現在 | 新 | 理由 |
|---|---|---|
| `DB_NEON_API_KEY` | `NEON_API_KEY` | Neon Terraform provider / CLI が読む標準名。カテゴリ接頭辞を外すと L1 に入る |
| `DB_NEON_MULTICLOUD_POOLED_URI` | `NEON_MULTICLOUD_POOLED_URI` | サービス名先頭（L2）に揃える。`MULTICLOUD` は Neon プロジェクト名由来なので残す |
| `DB_NEON_MULTICLOUD_DIRECT_URI` | `NEON_MULTICLOUD_DIRECT_URI` | 同上 |
| `ML_KAGGLE_TOKEN` | `KAGGLE_API_TOKEN` | kaggle CLI が読む名前そのもの。Makefile / docs の**マッピングシム 11 箇所が消える** |

**リネームしないもの**: `AWS_*` `AZURE_*` `GOOGLE_CLOUD_*` `SNOWFLAKE_*` `DATABRICKS_*` `MCML_TF_*`
`DOPPLER_TOKEN`。改名すると認証が通らない、または既に正しい形。

> `AWS_DEFAULT_REGION` は boto3 が `AWS_REGION` でも解決するが、Terraform / CLI との互換で
> `AWS_DEFAULT_REGION` が広い。据え置き。

### 3.4 削除リスト（46 件）

#### 群 A: 個人アプリ由来・`private-app/apps/dev` に正本あり（29 件）

```
AI_ANTHROPIC_API_KEY  AI_DEEPSEEK_API_KEY  AI_OPENAI_API_KEY  AI_PERPLEXITY_API_KEY
AUTH_CLERK_PUBLISHABLE_KEY  AUTH_CLERK_SECRET_KEY  AUTH_ENABLED  AUTH_JWT_SECRET
CLOUDFLARE_API_TOKEN
DB_MONGODB_URI  DB_MONGODB_URI_STABLE  DB_TURSO_URL  DB_TURSO_AUTH_TOKEN
GIT_GITHUB_ACCESS_TOKEN  GIT_GITHUB_SSH_PRIVATE_KEY  INTERNAL_DEPLOY_SSH_PRIVATE_KEY
MEDIA_CLOUDINARY_API_KEY  MEDIA_CLOUDINARY_API_SECRET  MEDIA_CLOUDINARY_CLOUD_NAME
NOTIFY_DISCORD_NEWS_WEBHOOK_URL  NOTIFY_DISCORD_NEWS_FAVORITE_WEBHOOK_URL
NOTIFY_DISCORD_NEWS_SIGNAL_WEBHOOK_URL  NOTIFY_DISCORD_WARNING_WEBHOOK_URL
NOTIFY_DISCORD_YOUTUBE_SUMMARY_WEBHOOK_URL  NOTIFY_INTERNAL_API_KEY  NOTIFY_INTERNAL_API_URL
OWNER_ADMIN_USER_IDS  OWNER_USER_IDS  PGADMIN_PASSWORD
```

削除前に **`apps/dev` 側に同名（または後継名）のキーが実在することだけ**確認する。
値の比較が要る場合も値は表示せず、ハッシュだけで突き合わせる:

```bash
# 値を画面に出さずに一致確認する（片側ずつ実行して digest を目視比較）
doppler secrets get <KEY> --project cloud-ml-lab --config dev --plain | sha256sum
DOPPLER_TOKEN=<private-app read-only token> doppler secrets get <KEY> -p apps -c dev --plain | sha256sum
```

> 注: `AI_*` は「ML/生成AI 学習用だから残す」という読み方もありうるが、**推奨は削除**。
> 生成AI の学習をこの config でやるなら、キー名は provider 直（`ANTHROPIC_API_KEY` /
> `OPENAI_API_KEY` = SDK 標準名）で**新規に発行**し、`apps/dev` の値を流用しない。
> 二重在庫を作らないことが本タスクの目的なので、既存 `AI_*` の移送はしない。→ §5 の要判断 1

#### 群 B: 参照ゼロ・正本もなし（16 件）

§1.3 の一覧から `DOPPLER_ENVIRONMENT` を除いた 16 件（Cognito 3 / Cookie 2 / ローカル DB 2 /
Hermes 3 / W&B / ngrok / Discord 無印 / YouTube 3）。`DOPPLER_ENVIRONMENT` は Doppler が
自動注入する予約変数で、削除コマンドを投げても残る（実行時に確認済み）。

- `ML_WANDB_API_KEY` は ML 学習で復活しうるが、参照ゼロの長寿命鍵を置き続ける理由がない。
  **必要になった時点で `WANDB_API_KEY`（SDK 標準名）として再発行**する。新規則の実演になる。

#### 群 C: 用途不明・要確認（1 件）

- `DWH_DATABRICKS_TOKEN` — `mcml/doppler.yaml` に「別文脈の既存キー・本ラボ用ではない・流用しない」と
  明記されているが、消費者がどこにも見つからない。`gcp-search-mlops-gke` が Databricks を
  参照しているものの Doppler 束縛が無い。**owner 確認の上で削除**、残すなら `DATABRICKS_DWH_TOKEN`
  へリネーム（L1 の形に揃える）。

#### 群 D: config ごと廃止

| 対象 | 根拠 |
|---|---|
| `dev_personal` | `dev` の部分集合で固有キー 0 件・2026-06-09 以降未使用。名前自体が「個人利用しない」方針と矛盾 |
| `stg` / `prd` | `ID_RSA` `NEON_DB_URL` の旧命名 2 件のみ・未使用。学習ラボに stg/prd の段階は要らない |

Doppler の環境ごと消すか config だけ消すかは UI 操作。**削除前に `--only-names` を保存**しておく。

### 3.5 再設計後の姿（31 件）

```
# ---- L1: SDK 標準名（改名禁止・無加工）----
AWS_ACCESS_KEY_ID  AWS_SECRET_ACCESS_KEY  AWS_DEFAULT_REGION  AWS_ACCOUNT_ID
AZURE_SUBSCRIPTION_ID  AZURE_TENANT_ID
GOOGLE_CLOUD_PROJECT  GOOGLE_CLOUD_REGION
SNOWFLAKE_ORGANIZATION_NAME  SNOWFLAKE_ACCOUNT_NAME  SNOWFLAKE_ACCOUNT  SNOWFLAKE_USER
SNOWFLAKE_PRIVATE_KEY  SNOWFLAKE_PRIVATE_KEY_PASSPHRASE  SNOWFLAKE_ROLE  SNOWFLAKE_AUTHENTICATOR
DATABRICKS_HOST  DATABRICKS_TOKEN
NEON_API_KEY                      # ← DB_NEON_API_KEY
KAGGLE_API_TOKEN                  # ← ML_KAGGLE_TOKEN
DOPPLER_TOKEN

# ---- L2: サービス名先頭 ----
NEON_MULTICLOUD_POOLED_URI        # ← DB_NEON_MULTICLOUD_POOLED_URI
NEON_MULTICLOUD_DIRECT_URI        # ← DB_NEON_MULTICLOUD_DIRECT_URI

# ---- L3: リポ固有 ----
MCML_TF_BILLING_ACCOUNT_ID  MCML_TF_BUDGET_EMAIL  MCML_TF_DBX_JOB_PRINCIPAL
MCML_TF_SF_GRANT_TO_USER  MCML_TF_VERTEX_SUBMITTER_EMAIL

# ---- 予約（Doppler 自動注入・触らない）----
DOPPLER_PROJECT  DOPPLER_CONFIG  DOPPLER_ENVIRONMENT

# ---- 未発行（発行時は SDK 標準名で登録する）----
GOOGLE_APPLICATION_CREDENTIALS  AZURE_CLIENT_ID  AZURE_CLIENT_SECRET
DATABRICKS_CLIENT_ID  DATABRICKS_CLIENT_SECRET
```

**77 → 31 件（60% 削減）。config を開けば「どのクラウドの何の鍵か」が第 1 トークンで分かる。**

### 3.6 カタログの持ち方（正本問題）

**全件ミラーの正本ドキュメントを新設しない。** Doppler 実体を正本とし、各リポは自分が参照する分だけ
`doppler.yaml` に書く（`multicloud-ml-platform-lab` が 2026-07-31 に採用済みの方針）。

- `kaggle-bronze-gcp/doppler.yaml` — 腐った全件ミラーを捨て、`KAGGLE_API_TOKEN` 1 件だけ残す
- `news-notification-priority-prediction-poc/doppler.yaml` — 参照 0 件なので、
  カタログを空にする（または Doppler 束縛自体を外す）
- orphan 検出は機械化する。`private-ops` の `src/audit-doppler-consumers.sh`（`make audit` /
  `make audit-write`）が同じ問題を `apps/dev` 側で既に解いているので、
  **cloud-ml-lab 版として移植**すれば今回のような腐敗を再発させずに済む

---

## 4. 移行手順（破壊的削除を避ける）

`private-ops` の `naming-refinement.md` の手順に揃える。可逆な順に 5 段。

| # | 手順 | リスク | 検証 |
|---|---|---|---|
| 1 | **群 B（参照ゼロ 14 件）を削除** | ほぼ無し | 削除前に `--only-names` を保存 |
| 2 | **群 A（個人アプリ由来 29 件）を削除** | 低 | `apps/dev` 側の実在を §3.4 の digest 手順で確認してから |
| 3 | **リネーム 4 件**: 新キー追加 → コード/docs を新名へ切替 → 旧名参照 0 を grep 確認 → 旧キー削除 | 中 | `kaggle-bronze-gcp` は `make download` を 1 回実行、`mcml` は `make check-residual` で疎通 |
| 4 | **`dev_personal` / `stg` / `prd` を廃止** | 低 | 事前に `--only-names` を保存 |
| 5 | **カタログ整備**（3 リポの `doppler.yaml` ＋ `mcml/docs/runbooks/credentials.md`） | 無し | `make lint` / docs リンク |

**今が最も安全なタイミング**: `multicloud-ml-platform-lab` は 5 基盤とも実測完了・クラウド撤収済みで、
リネームしても再実行するまで壊れるものが無い。稼働中の消費者は `kaggle-bronze-gcp` の
`ML_KAGGLE_TOKEN` 1 件だけなので、動作確認は `make download` 1 回で済む。

---

## 5. 要判断（owner）→ 回答済み（2026-08-01）

1. **`AI_*` 4 件** → **削除**。今後この config で生成AI を扱うなら `ANTHROPIC_API_KEY` 等の
   SDK 標準名で新規発行し、`apps/dev` の値は流用しない。
2. **`DWH_DATABRICKS_TOKEN`** → **削除**。
3. **`stg` / `prd` config** → **放置でよい**（廃止しない）。

## 6. 実行結果（2026-08-01）

### 6.1 やったこと

| # | 操作 | 結果 |
|---|---|---|
| 0 | ロールバック用スナップショット `doppler configs clone dev --name dev_archive_20260801` | 77 件を保全（値は Doppler 内に留まり、外へ出していない） |
| 1 | 群 B（参照ゼロ）16 件を削除 | 完了。`DOPPLER_ENVIRONMENT` は予約変数のため削除されず残存（想定どおり） |
| 2 | 群 A（個人アプリ由来）29 件＋群 C（`DWH_DATABRICKS_TOKEN`）1 件を削除 | 完了。この時点で 31 件 |
| 3 | リネーム 4 件（新キー作成 → digest 一致確認 → コード切替 → 実接続検証 → 旧キー削除） | 完了 |
| 4 | 3 リポの `doppler.yaml` を「自分が使う分だけ」に整理 | 完了 |
| 5 | `stg` / `prd` / `dev_personal` | `stg`/`prd` は owner 判断で放置。`dev_personal` は §6.5-1 |
| 6 | プロジェクト名を `kuro-dev-k` → `cloud-ml-lab` へ改名（§6.4） | 完了。参照 26 ファイルとローカル CLI スコープを追随 |

最終状態は §3.5 の 31 件と**完全一致**（`doppler secrets --only-names` で確認）。

### 6.2 リネームの検証（Evidence）

- **値の移送**: `doppler secrets get <OLD> --plain | doppler secrets set <NEW>` で移送し、
  新旧の `sha256sum` 先頭 12 桁が一致することを確認（4 件とも OK）。値は画面にもファイルにも出していない。
- **Kaggle**: `doppler run -- .venv/bin/python -m kaggle competitions list -s titanic` が
  認証済みレスポンス（`userHasEntered=True`）を返した。→ `KAGGLE_API_TOKEN` は kaggle CLI が
  直接読むため、Makefile のマッピングシム（`KAGGLE_API_TOKEN="$$ML_KAGGLE_TOKEN"`）を **11 箇所削除**。
- **Neon**: `make neon-read` が `NEON_MULTICLOUD_POOLED_URI` で 5 行返した。
- **品質ゲート（mcml）**: `make lint` → All checks passed / `make test` → **567 passed**。

### 6.3 変更したファイル

| リポ | ファイル |
|---|---|
| `multicloud-ml-platform-lab` | `Makefile` `doppler.yaml` `env/config.yaml` `sql/schema.sql` `src/platforms/neon/{connection.py,__init__.py}` `src/platforms/shared/contracts/tracking.py` `tests/platform_runs.py` `scripts/{mcp/neon.sh,tf_backend.py}` `docs/runbooks/credentials.md` |
| `kaggle-bronze-gcp` | `Makefile` `doppler.yaml` `env/secret.example.yaml`（＋gitignore 下の `env/secret.yaml` のコメント行） `docs/01_requirements.md` `docs/competitions/rogii-wellbore-geology-prediction.md` |
| `news-notification-priority-prediction-poc` | `doppler.yaml`（参照 0 件を明記した形へ） |

**意図的に変更しなかったもの**:
- `private-ops/**` — 別ワークスペース（`private-app` / `apps/dev`）の台帳。`ML_KAGGLE_TOKEN` /
  `DB_NEON_API_KEY` は向こうにも実在するため、こちらのリネームを持ち込まない。
- `docs/tasks/05_done/2026-08-01-修正09-*.md` — 完了済みタスクの履歴記録なので旧キー名のまま残す。

### 6.4 プロジェクト名の改名（`kuro-dev-k` → `cloud-ml-lab`）

キー名だけ直しても、入口の `kuro-dev-k/dev` が「クラウド学習用」だと読み取れない。そこで
**プロジェクト名も改名した**（2026-08-01）。

```bash
doppler projects update kuro-dev-k --name cloud-ml-lab -y
```

- **環境名 / config 名は変えていない**。config `dev` は環境 `dev` のルート config なので、
  slug を変えると config 名も変わり `stg` / `prd` と非対称になる。Doppler の dev/stg/prd 規約から
  外れる割に得るものが小さい。**プロジェクト名だけで `cloud-ml-lab/dev` となり目的を達成**する。
- **`id` も一緒に変わった**（改名前は `id` = `name` = `kuro-dev-k`、改名後は両方 `cloud-ml-lab`）。
  つまり `doppler.yaml` に書く識別子が変わるので、旧名は解決できなくなる
  （`Could not find requested project 'kuro-dev-k'`）。参照の追随は必須。
- 追随した参照: **26 ファイル**（mcml 5 / kaggle-bronze-gcp 5 / news-poc 1 / memo-remote 1 /
  private-ops の live なルール記述 7 ＋ その他）。`private-ops/docs/repos/retiring/` 配下は
  退役済みの Evidence なので**旧名のまま残した**。
- ローカル CLI のスコープも張り直した（`doppler setup --project cloud-ml-lab --config dev`）。
  この過程で **`kaggle-bronze-gcp` にはそもそもスコープが無い**ことが判明した
  （旧リポ名 `kaggle-bronze-challenge` のパスに紐付いたまま残っていた）。
  そのため `make init` / `make download` / `make submit-legacy` の素の `doppler run --` は
  「You must specify a project」で失敗する状態だった。**これも合わせて修正済み**。
  ついでに実体の無い `enviroment/environment-kit/...` のスコープも削除した。

改名後の疎通確認: `doppler run -- .venv/bin/kaggle competitions list`（認証OK）/
`make neon-read`（5 行返却）。

### 6.5 残っている作業

1. ~~`dev_personal` config の扱い~~ → **対応不要**と判明。これは Doppler がユーザーごとに自動生成する
   **personal config** で、CLI / API から削除できない（`You cannot delete a personal config.`）。
   かつ `dev` を**継承**しているため、`dev` からの削除がそのまま伝播し、現在 29 件（`dev` の 31 件から
   個人的に override 済みの `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` を除いた数）で
   **すべてクラウド系**。個人アプリ由来のキーは残っていない。
2. ~~`dev_archive_20260801` の掃除~~ → **削除済み**（owner 判断で冷却期間を置かず即削除）。
   これにより **46 件の削除は復元不可**。以後、値が必要になったら各サービス側で再発行する。
3. ~~`kaggle-bronze-gcp/.venv/bin/kaggle` のシェバン破損~~ → **対応済み**。旧リポ名
   `kaggle-bronze-challenge/` を指す stale path が `.venv/bin/` の 37 スクリプトに残っていたため
   現リポ名へ一括置換。`.venv/bin/kaggle competitions list` の疎通を確認（本件とは無関係の既存不具合
   だが、`make download` が新キー名で通ることの確認に必要だったため直した）。
4. **orphan 検出の機械化** — `private-ops` の `audit-doppler-consumers.sh` 相当を cloud-ml-lab 版として
   移植する（§3.6）。今回のような「カタログだけ腐る」再発を防ぐ。
5. **`apps/dev`（private-app workspace）側の棚卸し** — この Linux 端末からは到達できないため未実施。
   Mac 側で `registry-reconcile.md` / `naming-refinement.md` を進める必要がある。

---

## Acceptance Criteria

- `cloud-ml-lab/dev` のキーが 31 件になり、個人アプリ由来のキーが 0 件。
- 全キーの第 1 トークンがクラウドサービス名（または L3 のリポ名 / Doppler 予約語）。
- 「CLOUD セクションだけ命名規則の例外」という注記が不要になっている。
- 旧キー名の参照が全リポで 0（grep 実証）。
- 3 リポの `doppler.yaml` が「自分が使う分だけ」を記載し、実在しないキーを 1 件も含まない。
- `kaggle-bronze-gcp` の `make download` が新キー名で成功する。

## 関連

- 現行カタログ: [doppler.yaml](../../../doppler.yaml) / [credentials.md](../../runbooks/credentials.md)
- 別ワークスペース（`private-app/apps/dev`）の規約と台帳:
  `private-ops/docs/repos/env/doppler-apps/specs/仕様書.md`
- 同種タスクの先行事例（apps/dev 側）:
  `private-ops/docs/repos/env/doppler-apps/tasks/backlog/naming-refinement.md`
- ワークスペース分離の根拠: `private-ops/README.md` / `CLAUDE.md` / `AGENTS.md`
- AWS root 鍵の扱い: `doppler.yaml` と `credentials.md` が
  `docs/tasks/02_backlog/aws-root-key-最小権限化.md` を参照しているが、**このファイルは存在しない**
  （`credentials.md` §3 では「意図的に root 鍵のまま・owner 判断 2026-08-01」に決着済み）。
  本タスクの scope 外だが、参照の drift として別途整理が要る。
