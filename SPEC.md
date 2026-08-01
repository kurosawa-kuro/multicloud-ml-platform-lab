# SPEC.md

公式 Claude Code ワークフローの **「機能ごとの実装前スペック」**。
今から作る **1 機能** を、ここに自己完結で書いてから実装させる。

> **使い方（公式の Explore → Plan → Implement → Commit）**
> 1. **Explore**: plan モードで関係コードを読み、現状を掴む（編集しない）。
> 2. **Plan**: 下のテンプレを、触るファイル/IF を名指し・Non-scope 明記・末尾 E2E 検証付きで埋める。
> 3. **Implement**: 書けたら **新しいセッション** を開き「SPEC.md を実装して」で実行させる。
> 4. **Commit**: 完了したら要点を `docs/tasks/05_done/` または該当 `docs/` へ昇格し、本ファイルは次の機能で上書きする。
>
> **位置づけ（混同しない）**
> - `SPEC.md`（このファイル）= 今から作る **1 機能** の使い捨て実装スペック（WHAT+触る場所+検証）。
> - `docs/specs/` = **恒久アーキ設計**マスター（thin-harness / runtime-protocol / capability-boundary…）。SPEC.md とは別物。
> - `docs/tasks/` = タスク台帳（優先度・進行・evidence、番号付き5状態 `01_idea`→`05_done`）。SPEC.md の中身を実行に移したら `05_done/` へ要約。
> - 恒久仕様（要件/設計/ドメイン/データ）に昇格すべき内容は `docs/01〜05` を正本にし、ここへ二重管理しない。

---

## 機能名

<!-- 例: YouTube 要約の視聴判断トリアージ slice -->

## 1. Goal（何を満たせば完成か）

<!-- 1〜3 文。曖昧語禁止。「完成の定義」を先に固定する -->

## 2. Context / 現状

<!-- なぜ今これを作るか。関係する既存 runtime / DB / worker / job の現状を 1 段落で -->

## 3. 触るファイル・インターフェース（名指し）

<!-- 公式必須: どのファイル・関数・CLI・jobs row・テーブルに触るかを具体名で列挙 -->

- ファイル:
- インターフェース / CLI / job:
- DB / テーブル / カラム:

## 4. Scope（この機能でやること）

-

## 5. Non-scope（やらないこと）

<!-- 公式必須: scope creep を止める。「ついで修正/共通化/改名」はここに落とす -->

-

## 6. Plan（実装ステップ）

<!-- 小さい順に。Standard/Heavy はビジネスロジックレスの skeleton を先に置く -->

1.

## 7. Acceptance Criteria

<!-- 満たすべき観測可能な条件。happy-path 緑だけで OK にしない -->

-

## 8. End-to-End 検証（動作を証明する手順）

<!-- 公式必須: この機能が本当に動くことを示す具体コマンド/SQL/観測。
     本番に触る場合は Evidence Level 4（実 DB outcome / 通知・監視 surface で反証）まで。
     詳細は docs/specs/evidence-policy.md -->

```bash
# 例: make ... / 実DB SQL / dry-run 件数
```

期待結果:

## 9. Stop / Ask Owner If

<!-- protected 境界（本番/DB/Secret/IAM/Cloud）接触・前提崩れ・2 度違う理由で検証失敗 → 停止して owner へ -->

-
