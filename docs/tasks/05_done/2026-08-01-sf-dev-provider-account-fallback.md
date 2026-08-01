# sf-dev の plan が provider の account fallback で落ちる

状態: **完了（2026-08-01）**

Weight Class: Light（terraform 実行時の環境変数を1つ落とすだけ。実クラウド不要で再現・検証できる）
発見: 2026-08-01（修正09 の検証中。**修正09 とは無関係の既存問題**）

## Goal

`doppler run -- python scripts/run_terraform.py plan --env sf-dev` が通る状態にする。

```
Error: the account field requires the "PROVIDER_CONFIGURATION_ACCOUNT_FALLBACK"
experiment to be enabled; add it to experimental_features_enabled in provider configuration
```

## Value

failure detection / dev speed。5環境のうち sf-dev だけ plan が通らず、
修正04 の Acceptance「export なしで5環境とも plan できる」が **4/5 で止まっている**。

## Context — 原因は実測で確定済み（仮説ではない）

### 切り分け1: 修正09（backend の pg 化）は無関係

`backend.tf` を旧 GCS へ戻して `terraform init -reconfigure` しても**同じエラー**。
backend は `init` に効き、このエラーは provider 設定の評価時に出る。

### 切り分け2: 原因は `SNOWFLAKE_ACCOUNT` の存在

```bash
doppler run --command 'unset SNOWFLAKE_ACCOUNT && terraform -chdir=infra/environments/sf-dev plan ...'
→ Plan: 11 to add, 0 to change, 0 to destroy.   # 通る
```

`credentials.md §5` は env を意図的に使い分けている:

| env | 読む主体 | 形式 |
|---|---|---|
| `SNOWFLAKE_ORGANIZATION_NAME` + `SNOWFLAKE_ACCOUNT_NAME` | Terraform provider v2 | 分割 |
| `SNOWFLAKE_ACCOUNT` | Python connector | `<org>-<account>` |

provider 2.19 は **deprecated な `account` フィールドとして `SNOWFLAKE_ACCOUNT` を拾い**、
`experimental_features_enabled` を要求する。Phase 5（2026-08-01）は完走しているので、
その後に環境側で `SNOWFLAKE_ACCOUNT` が入った可能性が高い
（`.terraform.lock.hcl` は 2.19.0 のまま未変更）。

### 切り分け3: provider へ明示指定しても効かない（**この案は却下**）

`provider "snowflake"` に `organization_name` / `account_name` を明示しても同じエラー。
**値の解決順の問題ではなく、`SNOWFLAKE_ACCOUNT` が「存在すること」自体が判定される。**
provider 設定側では回避できない。

## Scope

`run_terraform.py` が terraform を起動するときだけ `SNOWFLAKE_ACCOUNT` を環境から落とす
（`sf-dev` のときのみ）。Python connector 側の env はそのまま残す。

## Non-scope

- `credentials.md` の env 使い分け方針の変更（Python connector が壊れる）
- Doppler から `SNOWFLAKE_ACCOUNT` を消す（同上。adapter が使っている）
- provider 版の変更（`~> 2.0` の制約と lock は触らない）
- Snowflake の実 apply（Phase 5 は完走済み。plan が通れば足りる）

## Plan

1. RED: 「`sf-dev` の terraform 実行環境に `SNOWFLAKE_ACCOUNT` が渡らない」テストを
   `tests/scripts/test_run_terraform.py` に足す（他4環境では落とさないことも固定）
2. `run_terraform.py` の `stream_command` へ env を渡せるようにし、
   `sf-dev` だけ `SNOWFLAKE_ACCOUNT` を除いた env で起動する
3. `doppler run -- python scripts/run_terraform.py plan --env sf-dev` が通ることを実測
4. 理由をコード側のコメントに残す（**なぜ落とすのかが分からないと将来戻される**）

## Acceptance Criteria

- [x] `plan --env sf-dev` が export なしで通る（**`Plan: 11 to add, 0 to change, 0 to destroy.`**）
- [x] 他4環境の plan が従来どおり通る（**gcp 18 / aws 15 / azure 10 / dbx 8** —— 全て期待値と一致）
- [x] Python connector 経路が壊れていない —— `adapter.py:98` の `ACCOUNT_ENV = "SNOWFLAKE_ACCOUNT"`
      は無傷で、`tests/platforms/test_snowflake_adapter.py` 21 passed
- [x] `make test` green（**574 passed**。追加した 6 件を含む）／ `make lint` All checks passed

## Stop / Ask Owner If

- 解決に `credentials.md` の env 使い分け方針の変更が要ると判明した場合
  （Python connector 側が壊れるため、方針変更は owner 判断）
- provider 版を上げる案に倒れる場合（lock 変更は Phase 5 の再現性に影響する）

→ どちらにも倒れず、Scope どおり `run_terraform.py` 内で解決した。

## 実施内容（2026-08-01・完了）

### 変更

| ファイル | 変更 |
|---|---|
| `scripts/run_terraform.py` | `TERRAFORM_ENV_BLOCKLIST`（env ごとの遮断表）と `terraform_environment()` を追加。`stream_command` に `env` 引数を足し、`run_terraform()` が `execute(command, terraform_environment(env))` で渡す |
| `tests/scripts/test_run_terraform.py` | runner 契約を `(command, env)` に更新（既存の代役 5 個）＋ 新規テスト 6 件 |

runner の契約を変えたのは、遮断が**実際に terraform 起動へ繋がっている**ことを
テストで押さえるため。純粋関数だけ緑でも、配線が無ければ何も直っていない。

### 追加したテスト（守る不変条件）

1. `sf-dev` では `SNOWFLAKE_ACCOUNT` が落ち、**それ以外の env は落ちない**
2. 他4環境は 1 変数も落とさない（遮断を広げると原因不明の解決失敗になる）
3. runner が受け取る env に `SNOWFLAKE_ACCOUNT` が無く、`PATH` は在る
   （空 env で起動すると terraform 自体が見つからなくなる事故の防止）
4. `adapter.py` が `SNOWFLAKE_ACCOUNT` を読み続けていること
   —— **「Doppler から消す」解決に将来倒れないための番人**

### なぜコメントを厚く残したか

「env を1つ落とす」は理由が分からないと将来必ず戻される。provider 側に
`organization_name` / `account_name` を明示しても効かないこと（＝値の解決順ではなく
env の存在自体が判定される）は、実際に試さないと分からない。この切り分け結果を
`terraform_environment()` の docstring に置いた。
