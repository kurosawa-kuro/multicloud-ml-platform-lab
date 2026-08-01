# 修正04: 手渡し TF_VAR を config.yaml / Doppler へ統合する

Weight Class: Standard（`infra/**` と `run_terraform.py`。apply はしない）
親調査: [2026-08-01-5基盤完走後の再設計と修正順序.md](./2026-08-01-5基盤完走後の再設計と修正順序.md) §2.2 / G1 / 順序#4

## Goal

runbook の本文にしか無い 10 種類の `TF_VAR_*` を廃し、
`run_terraform.py` が config.yaml（人が決める値）と Doppler（秘密）から
`-var` を組み立てる。**シェルの export という第3の設定出所を無くす。**

## Value

dev speed / docs canonicalization。再現性が runbook の読解に依存している状態の解消。
Databricks は4変数を渡し忘れると ① が落ちる（実測済み）。

## Context

親調査 §2.2 の表が対象10変数と分類仮説（H-4）:
config.yaml 行き = Databricks 4種 + Snowflake 3種 / Doppler 行き = メール系3種 + `neon_secret_string`。
`platforms/config.py` の設計原則「値の出所を2系統に分ける」を Terraform 入力側にも適用する形。

## Scope

- config.yaml に `terraform:` 相当のセクション（または各 platform 節への追加）を設計
- `run_terraform.py` が env ごとに `-var` / `-var-file` を組み立てる
- Doppler 行きの変数は環境変数参照（`TF_VAR_` への载せ替えを script 内で行う）
- 5 runbook から export 手順を削除し、「なぜこの値か」の説明だけ残す

## Non-scope

- terraform apply の実行（plan で同値確認まで）
- backend の変更（修正09）

## Plan

1. 10変数を H-4 の分類で確定（移せないものが出たら、それ自体を親調査へ追記）
2. RED: `run_terraform.py` の組み立てテスト（`tests/test_run_terraform.py` に追加）
3. 実装 → 5環境で `terraform plan` が現状と同値になることを確認
4. runbook 5本の export 手順を削除

## Acceptance Criteria

- [ ] `grep TF_VAR_ docs/runbooks/` が「説明のための言及」以外 0 件
- [ ] `doppler run -- make ENV=<env> tf-plan` が export なしで5環境とも通る
- [ ] 秘密（`neon_secret_string`）が config.yaml に**入っていない**
- [ ] `make test` green

## Stop / Ask Owner If

- config.yaml にも Doppler にも置けない変数が出た（H-4 の反例 = 設計判断が要る）
- plan が現状と差分を出した（値の取り違え。突き合わせて確認）
