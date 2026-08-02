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

## 2026-08-02 — `RunSink` の任意機能 duck-typing を廃し、`merge_run_params` を必須契約へ格上げする

- type: design-fix（応急処置からの格上げ）
- 何が起きていたか: `merge_run_params` は `RunSink` Protocol に無く、
  `TrackedOperations._merge_job_row_params` が `getattr(sink, "merge_run_params", None)` の
  **文字列一致**で拾っていた。それを持たない decorator（Experiments 複写）を1枚挟んだだけで
  **静かに何もせず戻り**、学習成功行の `params` が空 = stage を跨いだ再開（修正07）が壊れた。
  ユニットテストは全て緑のまま、実クラウドで初めて露見した。
- なぜ「その decorator に中継を足す」で終わらせなかったか: それは**その1枚を直しただけ**で、
  設計は「任意機能を名前で拾う」ままなので、**sink や decorator を足すたびに同じ穴が開く**。
  owner から「応急処置に見える。再設計すべきでは」と指摘され、根に戻した。
- 対応:
  1. `RunSink` を `@runtime_checkable` にし、`merge_run_params` を**必須メソッド**として宣言
  2. `_merge_job_row_params` の `getattr` を廃し、契約として素直に呼ぶ
  3. 書けない sink は「何もしない」と**明示的に**書く（`JsonlRunSink` は追記専用なので 0 を返す）
  4. decorator（`RecordingSink` / `VertexExperimentSink`）は中継を実装
  5. `tests/core/test_sink_contract.py` を追加。全 sink 実装の契約適合と、
     **一覧への追記漏れ検出**（src から `record_run` 定義クラスを走査して突き合わせ）まで見る。
     契約を1つ落とすと落ちることを実際に確かめた（実演で 2 failed）
- 影響範囲 (blast radius): 小。契約に1メソッド増えただけで挙動は不変。
  `make test` 601 passed。
- 教訓: **「ユニットテストが緑」と「契約を満たしている」は別**。任意機能を名前で拾う設計は、
  テストが緑のまま壊れる経路を作る。契約に入れて機械的に守る。
- link: [tracking.py](../../src/core/telemetry/tracking.py) /
  [test_sink_contract.py](../../tests/core/test_sink_contract.py)

## 2026-08-02 — 「記録の正本」と「run の観測」を層として分ける（応急処置からの再設計・第2弾）

- type: design-fix
- 発端: owner から「動いたが応急処置に見える。ちゃんと再設計すべきでは」と2度目の指摘。
  実際、直前の対応は (a) decorator に中継を1つ足す (b) 制約を docs に書く、で終わっていた。
- 誤っていた構造: Experiments 複写を **sink の decorator** として実装していたため、
  「Neon へ書く役」と「run を見る役」が同一物になっていた。帰結が2つ:
  1. **学習成功行が観測されなかった。** 成功行はジョブ側が書く規約なので
     `RecordingSink` が sink への伝播を抑制する。decorator は下流なので一緒に抑制され、
     最も情報量の多い行が Experiments に現れなかった（実クラウドで実測）。
     当初これを「sink decorator では原理的に届かない構造的制約」と説明したが、**誤り**。
     `RecordingSink` は成功行を受け取ってから抑制しており、run 自体は手元にあった。
     届かなかったのは設計の混同が原因で、制約ではない。
  2. **観測が記録の契約を背負っていた。** decorator が `merge_run_params` を落として
     stage 跨ぎの再開を静かに壊した。観測が記録の契約を持つ理由は無い。
- 対応: `core/telemetry/observers.py` に `RunObserver` / `NullObserver` を新設し、
  `TrackedOperations._tracked` が**抑制と無関係に**観測を呼ぶようにした。
  Experiments 複写は `VertexExperimentObserver` へ作り替え、**sink の契約を1つも持たない**
  （`record_run` も `next_attempt` も `merge_run_params` も無い＝落としようがない）。
  配線は `factory.build_observer`、adapter への注入は `attach_observer`
  （5基盤の `__init__` を変えないため。観測が誰かで adapter の挙動は変わらない）。
- 残る限界（**制約であることを実測で確認済み**）: 学習成功行の `metrics`（RMSE 等）は
  ジョブが書いた Neon の行にしか無い。学習コンテナは依存最小の制約で Vertex SDK を持てず
  （`docker/training/Dockerfile`）、ジョブ側から観測させることもできない。
  よって Experiments 上の学習 run は attempt / status / duration / params まで。
  **これは今度こそ構造的制約**で、`observers.py` の docstring に境界として明記した。
- 影響範囲 (blast radius): 中（記録経路の構造変更）だが挙動は不変。`make test` 601 passed。
- link: [observers.py](../../src/core/telemetry/observers.py)

## 2026-08-02 — 残留検査の穴（Experiments）と ADC quota project の回避策を設定へ落とす

- type: design-fix
- Experiments: `check_residual.py` に `experiment_run` を追加。SDK が作るので
  `terraform destroy` では消えず、`registered_model` と**同じ構造の穴**だった。
  実際、撤収後に run が2件残っているのに検査は「残留なし」と報告した。
  **課金がほぼ無いことと、検査から見えないことは別**（このモジュールの判定原理）。
  課金が継続しないので WARN（FAIL と同列にすると Endpoint の重大さが薄まる）。
- ADC quota project: `billingbudgets` API は ADC に quota project を要求し、
  予算アラートの作成だけが 403 で落ちていた。`gcloud auth application-default
  set-quota-project` だけでは**足りない**（実測）。環境変数
  （`USER_PROJECT_OVERRIDE` / `GOOGLE_BILLING_PROJECT`）で回避していたが、
  **それは手順書に書く回避策であって設定ではない** —— 知らない人が apply すると同じ 403 を踏み、
  ガードレールが黙って作られない。修正09 が潰したのはまさにその欠陥なので、
  provider 設定（`user_project_override` / `billing_project`）に固定した。
  env なしで `plan` が通ることを実測で確認。
- link: [check_residual.py](../../scripts/check_residual.py) /
  [gcp-dev/versions.tf](../../infra/environments/gcp-dev/versions.tf)

## 2026-08-02 — 設計の向きを反転する: 共通層は「5つのインフラの交差点」からしか導かない

- type: design-principle（owner 指摘「ML コード中心に考えるな。5つのインフラから共通 ML コードが
  どうあるべきかを考えないと永遠に適切な設計にならない。応急処置は原則無意味」）
- 何を間違え続けたか: Experiments 複写を「どう複写するか」（ML コード側の仕組み）から設計した。
  第1段（sink decorator）も第2段（core に RunObserver Protocol + 共通層フック）も、
  **実装が1つしか無い抽象を共通層に置いた**点で同型の誤り。`ports.py` は最初から
  「**5基盤ぶんの実装が並び、かつ呼び出し側が差を意識してはいけない操作だけ** port にする。
  それ以外は切らない」と定めており、リポの原則を自分の追加が破っていた。
- 最終形: 単一基盤の関心はその基盤に閉じる。`VertexAdapter._tracked` override +
  `VertexConfig.experiment`。共通層（core / TrackedOperations / factory）から observer の
  痕跡を全て除去し、`test_common_layers_do_not_know_the_observer` で機械的に固定した。
- あわせて発覚した仕様違反: 比較クエリが生の ml_runs / infra_events を読んでおり、
  campaign 後の検証 run が混入して UC-003 / G3 が破れていた。**母集団の定義を
  sql/schema.sql の view（baseline_runs / baseline_infra_events・境界 2026-08-01 15:00 UTC）
  として正本化**。view 経由の実行で failure_class 内訳（sdk 12 / container 1 / network 1 /
  package 1）と RMSE parity が記録と完全一致することを実測で確認した。
- 教訓: 共通コードの形は共通コードの中からは決まらない。**依存する側（5基盤）の制約の
  交差点だけが共通層に置ける**。1基盤にしか実装が無いものを共通層に置きたくなったら、
  それは設計の向きが逆になっているサイン。
- link: [ports.py](../../src/platforms/shared/contracts/ports.py) /
  [test_vertex_experiment_observer.py](../../tests/platforms/test_vertex_experiment_observer.py) /
  [schema.sql](../../sql/schema.sql)

## 2026-08-02 — 記録スパインを「5基盤×2経路の交差点」まで縮める（owner 指示の徹夜大改修）

- type: design-fix（root cause）
- owner の診断: 「一つの修正が根本設計を痛めて改修が要り、かつ5基盤それぞれに悪影響を出す。
  根本から再設計しないと永遠に安定しない」。監査で診断どおりの構造を確認した。
- 監査結果（波及の実測）: `merge_run_params` は**実装が意味を持つのは NeonRunSink の
  1つだけ**なのに、core の RunSink 契約の必須メソッドになっており、定義が4箇所
  （Protocol / Neon 実 / JSONL no-op / RecordingSink 中継）に増殖していた。
  さらにその呼び出しは**5基盤すべてが継承する `TrackedOperations._tracked` の中**。
  つまり resume という driver 機能の都合が、(a) core 契約 (b) 全 sink (c) 5基盤の
  共有基底、の3層へ波及する配線だった。この契約化は自分が2026-08-02朝に
  「再設計」と称して行ったもので、**片実装の操作を契約で均した時点で inside-out**。
- 再設計（outside-in の帰結）:
  1. **RunSink = record_run / next_attempt の2メソッドに固定**。これが direct / collected
     の2系統がともに意味を持って実装できる唯一の交差点。契約の形は
     `test_the_contract_is_pinned_to_the_intersection` が**メソッド集合ごと** pin し、
     太らせる変更は pin の意図的な書き換えなしに通らない
  2. **受け渡しの永続化は driver 機能へ**（`resume.persist_handoff`。読み側と同居）。
     系統差はここで吸収せず明示する: direct = merge / collected = **None で明示スキップ**
     （かつての契約 no-op は「できない」と「まだ無い」を 0 で混同していた）
  3. 共有基底（TrackedOperations）から merge の関心を全撤去。5基盤スパインは
     「1操作=1行・失敗も記録・attempt 採番・行所有」だけになった
- 波及半径の変化: before = RunSink に1メソッド足すと 4ファイル＋5 adapter 共有基底が
  連動 / after = 契約は pin されており、driver 機能の変更は resume.py と run_phase の
  呼び出し1箇所に閉じる
- 検証: make lint pass / make test 605 passed（handoff の新テスト12件を含む。
  Neon 実 merge の SQL は無変更なので実クラウド再検証は不要 —— 変わったのは呼び出し位置だけ）
- link: [core/telemetry/tracking.py](../../src/core/telemetry/tracking.py) /
  [resume.py](../../src/platforms/shared/resume.py) /
  [test_sink_contract.py](../../tests/core/test_sink_contract.py)

## 2026-08-02 — 実験追跡を5基盤へ展開: 実装は移植せず、形の差を残した

- type: adopted（導出順序の適用例）
- 依頼: 「Vertex から AWS / Azure も対応」。**Vertex の実装は移植しなかった。**
  導出順序どおり各基盤の制約を先に実測したところ、「実験追跡」は5基盤で形そのものが違った。
- 実測（installed SDK の service model / signature）:
  - Vertex … `ExperimentRun.create` = **事後 API**。全 stage を載せられる
  - SageMaker … `CreateTrainingJob.ExperimentConfig` = **投入時パラメータ**。
    `CreateModel` / `CreateEndpoint` / `CreateModelPackage` には口が**無い**（boto3 の
    service model で False を確認）＝ **学習だけが実験に載る**
  - Azure ML … `command(experiment_name=...)` = 投入時パラメータ。**job は常に実験に属す**
    （「無効」という状態が無い）。**既に実装済みだった**
  - Databricks … MLflow experiment のワークスペースパス = 基盤内蔵の別存在
  - Snowflake … **無い**
- 決定: 形の差は吸収せず、**フィールド名だけ揃える**（`experiment`。5基盤で同じ env 規約
  `MCML_<PLATFORM>_EXPERIMENT` で上書きできる）。既定値も揃えない ——
  Vertex / SageMaker は OFF を選べるが Azure / Databricks は選べないので、揃えると嘘になる。
  Snowflake は**フィールド自体を置かない**（空フィールドは「あるが未設定」に見え、
  比較レポートで「設定すれば使える」と誤読される）。
- 実装: SageMaker は投入リクエストに `ExperimentConfig` を載せた（事後 API は生やさない。
  Vertex の observer をコピーすると `ExperimentConfig` を使わない別経路ができ、
  「SageMaker Experiments に載せた」が嘘になる）。Azure / Databricks は改名のみ。
- 番人: `tests/platforms/test_experiment_tracking_asymmetry.py`（13件）。
  「同じ実装であること」ではなく**非対称そのもの**を守る。
- 検証: make lint pass / make test 628 passed（実クラウド未使用。SDK の service model と
  signature で制約を確定したので、形の判定に実行は不要）
- link: [02_architecture.md 制約表](../02_architecture.md) /
  [test_experiment_tracking_asymmetry.py](../../tests/platforms/test_experiment_tracking_asymmetry.py)

## 2026-08-02 — パイプライン化を AWS / Azure へ展開: 「Vertex で見送った」は転移しなかった

- type: adopted（導出順序の適用例・2件目）
- 依頼: 「パイプライン対応を Azure / AWS も」。**Vertex の結論（P3 見送り）を横展開しなかった。**
  各基盤の step の形を先に実測したところ、見送りの根拠そのものが AWS / Azure には無かった。
- 実測した差:
  - Vertex（KFP）… step = **コンテナ実行**。`run_phase.py` を動かす器
    （orchestrator イメージ）が要る。学習イメージは依存最小で `scripts/` も adapter も
    持たない → 実投入で exit 2。**P3 見送りは正しい**
  - SageMaker … step = **学習ジョブの型付き宣言**（`Training` step の `Arguments` は
    `CreateTrainingJob` のリクエストそのもの）。**間に立つコンテナが存在しない**
  - Azure ML … step = **command job の合成**（`dsl.pipeline`）。同上
  → 器の問題は Vertex 固有。AWS / Azure は adapter が既に組む仕様をそのまま載せられる。
- 実装: 両基盤とも「投入経路とパイプライン経路で**同じ関数**を使う」形にした
  （`SageMakerAdapter.training_request()` / `AzureMLAdapter.training_job()` を切り出し、
  CLI 投入もパイプラインも同じ戻り値を使う）。別々に組むと「CLI では通るが
  パイプラインでは落ちる」差が生まれ、比較が濁る。
  学習イメージは不変なので `job_record` の意味論（write_path / failure_class / attempt）も同一。
- 番人: `tests/platforms/test_pipeline_asymmetry.py`（9件）。同一仕様の再利用・
  **器を要求しないこと**（要求し始めたら Vertex と同じ結論になるサイン）・
  Vertex 実装が復活していないこと。
- 検証: 実 Terraform outputs で SageMaker のパイプライン定義が組めることを確認
  （クラウド未使用）／ make lint pass / make test 637 passed。**投入は未実施**。
- 教訓: 1基盤で出た結論を他基盤へ転移しない。**見送りの根拠まで含めて制約を確認する。**
- link: [02_architecture.md 制約表](../02_architecture.md) /
  [修正11 ノート](../tasks/04_verifying/2026-08-02-修正11-マネージドパイプライン載せ替え試行.md)
