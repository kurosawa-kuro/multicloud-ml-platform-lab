# Decision Log（判断日誌 / trade journal）

AI エージェント（と人間）が**実行中に下した判断**を時系列で残す append-only の日誌。
トレードで言う「売買日誌」に当たる: 何を・なぜそのサイズで賭けたか、損切りラインはどこか、結果はどうだったか。

## これは何で、ADR / task note と何が違うか

| | レンズ | 寿命 | 例 |
|---|---|---|---|
| `adr/` | **戦略** = アーキ決定（正本） | 長命 | 「runtime state はどこに置くか」 |
| `tasks/` | **個別建玉メモ** = 1タスクの作業記憶 | タスク中 | このタスクの Goal/Scope/証拠 |
| `decisions/`（ここ） | **売買日誌** = 通時の判断の振る舞い | 永続・追記専用 | 「曖昧 Goal にこの既定値で突っ込んだ」「scope外をdeferした」 |

task note は1タスクに閉じるので「最近このエージェントは曖昧スペックにフルレバで入りがち」といった**通時のレビュー**ができない。この日誌はそこを埋める。

## 運用ルール

- **append-only**。過去エントリは書き換えない。結果が後で判明したら `結果` 行だけ追記更新してよい。
- 新しいエントリは**末尾に足す**（古い順・上から下）。
- `--- session boundary <ts> ---` 行はセッション終了時に Stop hook が自動で挿入する。判断ゼロのセッションでは挿入されない。
- 何を書くかの引き金と手順は `.claude/skills/log-decision/` が正本。
- **secret・トークン・cookie・個人パス・実データを書かない**（`.claude/rules/security.md`）。判断の構造だけ残す。

## エントリ形式

```markdown
## <UTCタイムスタンプ> — <判断の一行要約>
- type: default-taken | scope-cut | approach-choice | rollback | risk-accepted | approval-deferred
- 根拠 (why): なぜこの判断か（＝建玉理由 / entry thesis）
- 影響範囲 (blast radius): 何が壊れうるか・可逆性（＝レバ/ロットサイズ）
- 撤退条件 (stop/revert): 何が起きたら戻すか・どう戻すか（＝損切りライン）
- 結果 (outcome): win | loss | open | 塩漬け（後追い更新可）
- link: task note / ADR / PR（任意）
```

---

## 2026-08-01 — Snowflake は専用サービスユーザーを作らず、ロールで権限を分ける

- type: approach-choice
- 根拠 (why): トライアルアカウント作成時に RSA 公開鍵が人間ユーザー（ACCOUNTADMIN）へ付いた。
  `credentials.md §5` は人間と分離した service user を求めるが、30日のタイムボックスに対して
  ユーザーを1つ増やす手数の見返りが小さい。**分離が本当に必要なのは「誰が接続するか」ではなく
  「どの権限で ML 操作が走るか」**で、そちらはロールで担保できる。Terraform は ACCOUNTADMIN
  （Resource Monitor 作成に必要）、adapter は Terraform が作る `MCML_DEV_ROLE`。
- 影響範囲 (blast radius): 中。ロール分離を怠ると Snowflake だけ permission friction が
  常にゼロになり、**このラボの本命の計測値が1基盤ぶん欠ける**。可逆（後から service user を
  切って `SNOWFLAKE_USER` を差し替えるだけ）。
- 撤退条件 (stop/revert): 人間ユーザーの MFA 必須化でキーペア接続が弾かれたら、
  `TYPE = SERVICE` のユーザーを切って移す。回避策（MFA 除外等）で粘らない。
- 結果 (outcome): open（Phase 5 着手時に実接続で判定）
- link: [動作検証-snowflake.md](../runbooks/動作検証-snowflake.md) / `SnowflakeConfig.role`

## 2026-08-01 — Snowpark の接続パラメータを adapter 側で明示的に組む

- type: approach-choice
- 根拠 (why): `Session.builder.configs()` に1つでも値を渡すと connections.toml が読まれず、
  connector 側に `SNOWFLAKE_ACCOUNT` / `SNOWFLAKE_USER` の env フォールバックも無い
  （snowpark 1.42 / connector 4.7 のソースと実行で確認。`251005: User is empty`）。
  connections.toml を使う案は、秘密の置き場が Doppler から離れるので採らない。
- 影響範囲 (blast radius): 小。`platforms/snowflake` に閉じる。他4基盤の資格情報解決は
  各 SDK のチェーンに委ねたままで、この基盤だけ非対称になる。
- 撤退条件 (stop/revert): SDK 側が env フォールバックを実装したら、この組み立てを捨てる。
- 結果 (outcome): open
- link: `connection_parameters()` / tests/test_snowflake_adapter.py

## 2026-08-01 — Databricks は OAuth SP を見送り、PAT を Doppler に置く

- type: risk-accepted
- 根拠 (why): `credentials.md §6` は OAuth M2M（SP）を推奨し PAT は「手元検証のみ・
  `env/secret.yaml` 止まり」としていたが、Free Edition では SP 発行に要るアカウントコンソール
  権限が無い見込み。`make` は `doppler run --` 経由なので、PAT を `env/secret.yaml` に置くと
  そもそも渡らない。**方針を守ると動かない**ので、保管先ごと Doppler に寄せる。
- 影響範囲 (blast radius): 小〜中。PAT は長寿命の広い権限を1本持つ形になる（SP なら権限を
  絞れた）。漏洩時の影響はワークスペース全体。可逆（SP を作れれば差し替えるだけ）。
- 撤退条件 (stop/revert): 有償プランへ移行するか SP を発行できると分かった時点で移行する。
  PAT の失効で Phase 3 が止まったら、延長せず**期限を計測値として記録**してから再発行する。
- 結果 (outcome): open
- link: [credentials.md §6](../runbooks/credentials.md) / databricks-phase-precheck.md

## 2026-08-01 — Azure を無料試用版から Pay-As-You-Go へアップグレードする（owner 承認）

- type: risk-accepted
- 根拠 (why): 無料試用版は offer レベルで AML の専用コアを持たず、**compute cluster が作れない**
  （apply 3回とも同じ壁）。リージョン変更 / serverless / VM サイズ縮小 / `az ml compute
  update-quota` は全て実測で無効と判明し、**技術的な回避策が尽きた**。残る経路は契約変更のみ。
  残クレジット ¥32,777 が引き継がれるため当面の実費は ¥0 で、承認済みの月額枠内に収まる。
- 影響範囲 (blast radius): 中。**`spendingLimit` が Off になり自動停止が消える** —
  Endpoint の消し忘れがそのまま実費請求になる。可逆性が非対称で、
  **アップグレードは戻せない**（無料試用版への復帰経路が存在しない）。
  下げる手段はリソース削除とサブスクリプション取り消しだけ。
  代償制御として、既に module にある `azurerm_consumption_budget_resource_group` を
  Phase 4 の apply から有効化する（`TF_VAR_budget_notification_email`）。
  **ただし予算アラートは通知のみで停止はしない** — 止められるのは destroy だけ、という
  非対称をこの基盤の性質として記録しておく。
- 撤退条件 (stop/revert): クレジット失効 **2026-08-30** までに Phase 4 を完走できなければ、
  延長を試みず [動作検証-azureml.md §9](../runbooks/動作検証-azureml.md) 段階1（リソース削除）で
  Azure を切り離す（`01_requirements.md` の「Azure は条件付き」に従う）。
  月次が ¥2,000 を超えた場合も同じ。
- 結果 (outcome): **成功。① terraform apply が10リソースで完走した**（compute cluster
  `mcml-dev-cpu` = `State=Succeeded`・予算アラート有効）。反映まで約2分（45秒間隔ポーリングの
  3回目で `quotaId=PayAsYouGo_2014-09-01` / `spendingLimit=Off`）。
  **ただし壁は2枚あった。** offer を越えた直後、`vm_priority = "LowPriority"` が
  `TotalLowPriorityCores 0/0` に当たり apply が 9/10 で失敗（`ClusterMinNodesExceedCoreQuota`）。
  dedicated 枠（`0/20` / family `0/6`）は足りていたため、**`Dedicated` へ切り替えて解決**
  （`azure-dev/variables.tf` に固定）。low-priority 枠はセルフサービス引き上げ不可なので、
  **Spot 相当を使うという設計判断自体がこの契約段階では成立しない**。
  なお当初「quota 引き上げ不要」と判断したのは dedicated 枠だけを見た誤りで、
  再発防止として runbook §1 の点検コマンドに枠種別（`.type`）の表示を追加した。
  **その後 owner がプランを Free → Basic へ変更し、同じ `az quota update` が通るようになった**
  （`TotalLowPriorityCores` 0 → 8・約90秒で承認）。`vm_priority` を `LowPriority` へ戻して
  再 apply し、`tier=low_priority` / `State=Succeeded` で稼働（77.2s・置換1件）。
  **Vertex の Spot と実行形態が揃った**ので、Dedicated への退避は解消した。
  申請は総枠（`TotalLowPriorityCores`）でしか受け付けられず、family 単位
  （`--resource-type lowPriority`）と `TotalDedicatedCores` は不可という制約も実測で確定。
- link: [動作検証-azureml.md §1/§9](../runbooks/動作検証-azureml.md) /
  [04_azureml.md「詰まった点」](../comparison/04_azureml.md)

## 2026-08-01 — AWS の root アクセスキーを最小権限 IAM へ差し替えない（owner 判断）

- type: risk-accepted
- 根拠 (why): 個人ラボ・単一 owner・短命な検証用アカウントという規模に対し、
  IAM ユーザー作成とポリシー設計の手数が見合わない（owner が「オーバーエンジニアリング」と判断）。
  backlog task は削除した。**再提案しない。**
- 影響範囲 (blast radius): 中だが限定的。漏洩時の被害に上限が無い一方、
  実害は2点に閉じる —— ① SageMaker の**投入側** permission friction が恒久的に測れない
  （実行ロール側は最小権限なので測れており、比較の主要部分は成立する）、
  ② AWS MCP の `--read-only` を外せない（これが唯一のガードレール）。
- 撤退条件 (stop/revert): このアカウントを個人ラボ以外に使う、他人に渡す、
  長期運用へ移す —— いずれかが起きたら差し替える。それまでは触らない。
- 結果 (outcome): 対応しないことで確定。`credentials.md §3` と `02_sagemaker.md` に
  「未計測」「意図的にそのまま」と明記済み。
- link: [credentials.md §3](../runbooks/credentials.md) / [02_sagemaker.md](../comparison/02_sagemaker.md)

## 2026-08-02 — 公開のため git 履歴を作り直し、5基盤 run の code_revision 検証を名指し免除にする（owner 判断）

- type: risk-accepted
- 背景: 公開監査でクレデンシャルは 0 件だったが、実クラウドの識別子（AWS アカウント ID・
  GCP プロジェクト ID・Databricks ワークスペース URL・Snowflake アカウント/ユーザー・
  個人メール）が追跡ファイルに残り、履歴にも AWS アカウント ID 21 回・`KUROMAILSERVER`
  99 回出ていた。ファイルを直しても公開リポの履歴からは消えない。
- 根拠 (why): 履歴書き換え（filter-repo）は既存 SHA を全て変え、`ml_runs.code_revision` と
  `docs/comparison/` の対応を壊す。一次データとの紐付けを守るため、**private の履歴は書き換えず
  作り直す**方を選んだ（owner が `rm -rf .git && git init` を実行）。
- 影響範囲 (blast radius): **`src/core/ml` の tree 一致を「再検証する手段」が5基盤 run について
  永久に失われた**。検証した事実そのものは残る（リセット前に実測し tree hash は5つとも
  `a1b73934` で一致。記録は `test_code_revision_parity.py` の docstring と `docs/comparison/`）。
  ただし値を再導出できないので、定数と突き合わせるテストは同語反復になり書けない。
- 対応: `PRE_HISTORY_RESET_REVISIONS` に**5 SHA を名指し**して免除。
  「解決できないものは飛ばす」には**しない** —— それをやると埋め込み漏れや別リポ由来の SHA を
  持つ新しい run が黙って通る。名指し以外の未解決 SHA は
  `test_only_pre_reset_revisions_are_unresolvable` が落とす（偽 SHA を混ぜて発火を実証済み）。
- 撤退条件 (stop/revert): 5基盤を再実行して新しい run を記録できたら、免除リストを削除し
  元の「全 run のコミットが実在する」形へ戻す。
- 結果 (outcome): `make test` 574 passed。以後の run はこのゲートが本来の強さで見る。
- link: [test_code_revision_parity.py](../../tests/comparison/test_code_revision_parity.py) /
  [credentials.md §0-a](../runbooks/credentials.md)

## 2026-08-02 — 実験管理は A（併存）を採用。Neon を正本のまま Vertex の run だけ複写する（owner 判断）

- type: adopted
- 選択肢: **A 併存**（採用）/ B 置換（Neon をやめ横断集計層を自前で作る）/ C 不採用
- 根拠 (why): B は3点で敗着。① 要件 UC-003「Neon の SELECT だけで5基盤比較」が単一テーブル
  前提で、Experiments は Vertex のサービスなので置換すると横断集計層を**自前で**作ることになり
  「自前実装を減らす」動機と矛盾する。② `attempt` は Neon の同一 (platform, stage) 過去行数+1 が
  正本（`platforms/shared/contracts/tracking.py:87`）で、移すと permission friction が別物になる。
  ③ SDK 1.163.0 の `log_params` は `Dict[str, float|int|str]` しか受けず実行時に型検査するため、
  比較6列のうち native に載るのは `status`（`Execution.State`）だけで残り5列は generic な params 止まり
  ＝**格納はできるが比較軸として引けない**。C は、基盤ネイティブの実験管理の見え方自体が
  比較材料であり、複写コストが decorator 1枚 + 既定 OFF に収まったので退けた。
- 影響範囲 (blast radius): 小。`RunSink` の decorator 1枚で、`PlatformAdapter` /
  `ml_runs` スキーマ / Tier B adapter は不変。`MCML_VERTEX_EXPERIMENT` 未設定なら経路も変わらない。
- 撤退条件 (stop/revert): Experiments 対応のために `RunSink` 契約か `_tracked()` を
  変えたくなったら、それは A が成り立っていない証拠なので C（不採用）へ倒す。
- 結果 (outcome): 実装済み（`src/platforms/vertex/experiment_sink.py`）。`make test` 588 passed。
  **実クラウドでの複写確認は未実施** —— owner 判断で後続のクラウド作業とまとめて一括検証する。
- link: [修正10 タスクノート](../tasks/04_verifying/2026-08-02-修正10-マネージド実験管理載せ替え試行.md) /
  [credentials.md §1-a](../runbooks/credentials.md)

## 2026-08-02 — パイプライン載せ替えは「既存イメージで載る」が実投入で反証された（記録）

- type: hypothesis-refuted
- 何を主張していたか: Core を読んだ再調査で「パイプラインの各ステップ = 既存の学習イメージ +
  `run_phase.py vertex <stage>` の合成で組め、Core 変更ゼロで載る」と結論し、compile が
  通ったことをもって実装完了としていた。
- 何が起きたか: 実投入で `run-stage` が **exit status 2** で即失敗
  （PipelineJob `mcml-vertex-train-register-20260802022521`）。学習イメージには
  `scripts/` も vertex adapter も `google-cloud-aiplatform` も入っていない（実測）。
  これは `docker/training/Dockerfile` と `pyproject.toml` の `gcp` extra が明記する
  **依存最小の設計どおり**であり、読み違えたのはこちら。
- 読み違いの構造: 「stage が別プロセスで独立実行できる」（修正07 の成果・正しい）と
  「学習イメージの中で実行できる」を同一視した。**compile は image と command の文字列を
  検証しないので、静的検証の射程外だった。** 「compile が通る」を「動く」の証拠にしない。
- 影響範囲 (blast radius): 小。Core は無変更で、追加したのはパイプライン定義とコンパイル
  スクリプトのみ。DAG の形・依存順・キャッシュ既定・env 選別は実測でも意図どおりだった。
- 次の選択肢: P1 オーケストレータイメージを足す / P2 ステップを学習 CLI そのものに寄せる /
  P3 パイプライン化を見送る。**owner 判断待ち**（`04_verifying` の修正11 ノート）。
- 教訓: 「ブランチ実験は不要、調査で分かる」は修正10 では正しかったが、
  **実行環境の中身に依存する主張は静的検証では閉じない**。
- link: [修正11 ノート](../tasks/04_verifying/2026-08-02-修正11-マネージドパイプライン載せ替え試行.md)
