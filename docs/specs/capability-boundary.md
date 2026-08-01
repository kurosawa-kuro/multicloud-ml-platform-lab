# Capability Boundary（このプロジェクトの instantiation）

**Layer 4: Capability Boundary**（§12）の repo 固有 instantiation。マスター文書 [`kurosawa-thin-harness-architecture.md`](./kurosawa-thin-harness-architecture.md) は保護対象を一般的に列挙する。このファイルは*このリポジトリ*のツールと `.claude/settings.json` の強制層へ写像する。Skill: `assess-risk`。

> **使い方（厚いメニュー → 削る）**: 下表は「個人プロジェクトでよく出る保護対象」を**安全既定で厚く列挙**したもの。**このプロジェクトに該当しない行は削る**。薄い TODO から足すのでなく、厚い既定から削る運用にする（テンプレ＝最厚、コピー先で削る）。

## 脅威モデルを先に（このプロジェクトで壊れたら困るもの）

> このプロジェクトの **loss-critical 資産**を1〜数点で言語化してここに書く（例: 本番DB / マスターデータ / 顧客データ / secret / 課金アカウント）。
> 「壊れても再構築でき金銭損害ゼロ」なら該当ツールを ask→allow に緩める。loss-critical があるなら ask/deny を厚くする。重心は守る対象で決まる。

## 強制マップ（settings.json と一致させる。該当しない行は削る）

| Capability | よくあるツール | 既定 | 備考 |
|---|---|---|---|
| 安全な開発ループ | `make test`/`lint`/`build`、`cargo *`/`npm test`/`pytest`、read-only git | **allow** | 常に allow |
| ファイル検索/閲覧 | `rg`/`grep`/`ls`/`find`/`cat`/`head`/`tail` | **allow** | |
| 生インフラ/DBツール | `gcloud`/`aws`/`az`/`terraform`/`pulumi`/`kubectl`/`psql`/`mysql`/`mongosh`/`turso`/`supabase` | **ask** | 脅威モデル次第で allow（再構築可なら） |
| secret 注入ランナー | `doppler run`/`vault`/`op run` | **allow** | ランナー自体は allow。ask にすると非対話/auto で classifier hard-block になり標準ランナーが全滅する。注入は無害で、危険は次行の secret write と実行先コマンド側（allow/deny で別途 governed） |
| secret push/同期 | GCP Secret Manager / AWS SM / `doppler secrets set` | **ask** | secret の**書き込み**はここ（`doppler run` の read 注入と区別する） |
| 本番デプロイ | `gcloud run deploy`/`vercel --prod`/`fly deploy`/`kubectl apply`/`serverless deploy` | **ask** | |
| DB schema/migration | `*-migrate`/`alembic`/`prisma migrate`/`sqlx migrate`/`drizzle push` | **ask** | 本番なら owner approval |
| 課金/quota 変更 | billing API、plan 変更、quota 引き上げ | **ask** | |
| paid AI API 実行 | LLM/画像/音声の有料呼び出し（バルク） | **ask** | budget gate 併用 |
| 外部通知送信 | Slack/Discord/email/webhook/LINE 送信 | **ask** | dry-run があれば allow |
| パッケージ公開 | `npm publish`/`cargo publish`/`pypi upload`/`gh release` | **ask** | |
| DNS/ドメイン | DNS レコード変更、ドメイン購入 | **ask** | |
| オブジェクトストレージ | 本番バケットへの write/delete（`gsutil`/`aws s3`） | **ask** | |
| 保護ファイルパス編集 | `env/secret/**`、`infra/**`、`terraform/**`、`.github/workflows/**`、`**/migrations/**` | **`detect-safety-boundary` hook (exit 2)** | `change-boundary.md` と一致させる |
| 不可逆なコード/履歴破壊 | `rm -rf`、`git push --force*`、`git reset --hard`、`git clean -fdx` | **deny** | 常に deny |

## 保護 capability チェックリスト（`assess-risk` / `docs/templates/capability-boundary.md`）

- [ ] 本番デプロイ
- [ ] DB migration / schema 変更
- [ ] secret read/write
- [ ] IAM / 権限変更
- [ ] クラウドリソース作成/削除
- [ ] 課金 / quota
- [ ] 破壊的コマンド
- [ ] 本番 / マスターデータアクセス
- [ ] 認証 / 認可ロジック
- [ ] パッケージ公開 / リリース

## ルール

- `ask`/`deny` は*コマンド*の正本。`detect-safety-boundary` hook は*ファイル編集*をカバーする（コマンド層が見ない隙間）。
- この設定は**このプロジェクト固有**。脅威モデルが違う他プロジェクトへそのまま移植しない。
- メニューを削った/緩めた理由は `docs/decisions/decision-log.md` に1行残す。
