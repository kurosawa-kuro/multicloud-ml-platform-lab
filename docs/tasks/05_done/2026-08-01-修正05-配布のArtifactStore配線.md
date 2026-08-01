# 修正05: 配布（データ・イメージ）を ArtifactStore と make に配線する

Weight Class: Standard（実クラウドへの upload を含むが、対象は dev リソースのみ）
親調査: [2026-08-01-5基盤完走後の再設計と修正順序.md](./2026-08-01-5基盤完走後の再設計と修正順序.md) §2.3 / A-4 / G1 / 順序#5

## Goal

実装・テスト済みで死蔵中の `GcsArtifactStore` / `S3ArtifactStore` / `BlobArtifactStore`
（`src/platforms/artifacts.py`）に CLI の口を作り、Tier A 3基盤の
「§3 配布物の準備（自動化なし・手打ち）」を `make distribute PLATFORM=<p>` 相当の
1コマンドにする。SageMaker serving の manifest 特殊手順（`buildx --oci-mediatypes=false`）も
Makefile に畳む。

## Value

dev speed / docs canonicalization。修正04と合わせて G1（1コマンド + outputs で再現）が完成する。
runbook の手打ちコマンド起因の事故（RBAC 不足で upload 失敗等）を手順から検出可能な失敗に変える。

## Context

- 呼び出し箇所 grep の実測: `train_pipeline.py` のコメント3行のみ。実行経路ゼロ
- `動作検証-sagemaker.md §3` が「CLI から呼ぶ口が無いので手打ち」と自認
- Tier B は adapter 内に upload 実装済み（配線の非対称）
- SageMaker の OCI index 拒否は **Training は通り ⑤ で初めて落ちる**罠（A-4）

## Scope

- `scripts/` に配布 CLI（データ Parquet + 学習/推論イメージの push）を追加
- Makefile ターゲット（`distribute` 等。既存の `docker-build` 系と整合させる）
- SageMaker serving の manifest 形式チェック（push 後に media type を検証して落とす）
- Tier A 3 runbook の §3 を1コマンドに書き換え

## Non-scope

- Tier B の配布変更（adapter 内で配線済み）
- ArtifactStore の Protocol 変更（`upload_dir` / `download_dir` のまま）

## Plan

1. RED: CLI の引数解決と store 選択のテスト（クライアントは注入・実クラウド不要）
2. 実装 → `make test`
3. 実クラウドで1基盤（コスト最小の Vertex）だけ実 upload を確認
4. runbook 3本を更新

## Acceptance Criteria

- [ ] `make distribute PLATFORM=vertex|sagemaker|azureml` が outputs.json だけで動く
- [ ] SageMaker serving push 後の manifest が
      `application/vnd.docker.distribution.manifest.v2+json` であることを機械検証
- [ ] runbook §3 から手打ちコマンド列が消えている
- [ ] `make test` green

## Stop / Ask Owner If

- 実 upload 検証で月次コスト見込みが変わる操作が要る場合（現状想定はストレージ数円のみ）
