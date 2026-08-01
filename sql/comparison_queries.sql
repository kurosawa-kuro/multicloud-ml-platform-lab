-- 比較 SELECT の正本（定番4本 + 補助1本）。
--
-- 「Neon の SELECT だけで5基盤の比較ができる」状態を保つことが要件
-- （docs/01_requirements.md UC-003）。レポート docs/comparison/ の各表は
-- ここのクエリ結果から起こす。手で数えた値をレポートに書かない。
--
-- スキーマ正本は sql/schema.sql。参照整合は tests/test_sql_contracts.py が pin する。
--
-- **生の ml_runs / infra_events は読まない。** 読むのは baseline_runs /
-- baseline_infra_events（sql/schema.sql の比較母集団 view）。テーブルは追記専用なので、
-- campaign 後の検証 run・実験 run が増えるたびに生テーブル直読みの数字は変わってしまう
-- （2026-08-02 に実際に起きた）。母集団の定義は view 側が正本。

-- teardown 行の除外条件（2026-08-01 以降の全クエリで使う）。
--
--   旧形式（2026-08-01 の実測分）: stage='deploy' + params->>'action'='teardown'
--   新形式:                        stage='teardown'
--
-- 過去行は書き換えない規約（「計測データを消さない」）なので、
-- **両形式を弾く条件を各クエリに書く**。これを忘れると deploy の
-- attempt と所要（332〜547s）に teardown が混ざる。

-- 1. metric parity
--    同一の学習コード・同一seed なら5基盤で RMSE が一致するはず。
--    distinct_rmse が 1 でなければ、基盤の差ではなく実装漏れ
--    （データ層かパッケージ版の差を疑う。特に Snowflake の Anaconda channel 版）。
--    tests/test_metric_parity.py が機械判定する対象。
--
--    ⚠️ **code_revision で group by しない。** 5基盤を順に回すと間に
--    adapter / docs のコミットが挟まり、記録される SHA は基盤ごとに変わる
--    （2026-08-01 実測: 5基盤とも別 SHA。ただし `src/core/ml` の tree hash は一致）。
--    SHA で束ねると「1 SHA あたり 1 基盤」に割れて parity を示せない。
--    学習コードの同一性は tests/test_code_revision_parity.py が tree hash で検証する。
select count(distinct platform)                        as platforms,        -- 5 であること
       count(distinct metrics ->> 'rmse')              as distinct_rmse,    -- 1 なら parity 成立
       min(metrics ->> 'rmse')                         as rmse_min,
       max(metrics ->> 'rmse')                         as rmse_max,
       array_agg(distinct platform order by platform)  as platform_list,
       array_agg(distinct code_revision)               as code_revisions    -- 参考: 揃わなくてよい
from baseline_runs
where stage = 'train'
  and status = 'success'
  and metrics ? 'rmse';

-- 2. permission friction（本命）
--    最小権限で通るまでに何回直したか。attempts_until_success が「試行回数」の本体。
--    成功が無い行（NULL）は「まだ通っていない」を意味する。
--
--    ⚠️ **記録された `attempt` 列をそのまま使わない。**
--    2026-08-01 まで teardown は `stage='deploy'` として記録され、
--    attempt カウンタ（`next_attempt(platform, stage)`）を deploy と共有していた。
--    その結果、撤退が attempt 番号を先に消費している
--    （実測: snowflake の実 deploy は撤退が attempt=1 を取ったため attempt=2 で記録）。
--    そこで **teardown を除いた行の中での順位を数え直す**。
--    新形式（stage='teardown'）でも旧形式でも同じ答えになる。
with runs as (
    select platform,
           stage,
           status,
           row_number() over (partition by platform, stage order by created_at) as try_number
    from baseline_runs
    where params ->> 'action' is distinct from 'teardown'
)
select platform,
       stage,
       min(try_number) filter (where status = 'success') as attempts_until_success,
       count(*) filter (where status = 'failure')        as recorded_failures
from runs
group by platform, stage
order by platform, stage;

-- 2b. failure_class 別の内訳（選定チェックリストの中核）
select platform,
       stage,
       failure_class,
       count(*) as failures
from baseline_runs
where status = 'failure'
  and params ->> 'action' is distinct from 'teardown'
group by platform, stage, failure_class
order by platform, stage, failures desc;

-- 3. stage 別所要時間
--    train / register / deploy / predict / teardown のどこに時間が偏るか。
--    Tier A と Tier B で形が変わるはず（Snowflake は deploy が「無い」に近い等）。
--
--    旧形式の teardown 行を `teardown` として読み替える（deploy の平均を汚さない）。
--    2026-08-01 実測では Tier A の deploy 513〜547s / teardown 332〜335s で、
--    混ぜると deploy の avg が実態より短く出ていた。
select platform,
       tier,
       case when params ->> 'action' = 'teardown' then 'teardown' else stage end as stage,
       count(*) filter (where status = 'success')                                as success_runs,
       round(avg(duration_seconds) filter (where status = 'success')::numeric, 1) as avg_seconds,
       round(max(duration_seconds)::numeric, 1)                                   as max_seconds
from baseline_runs
group by platform, tier, 3
order by 3, platform;

-- 4. teardown 品質
--    destroy 後に何が残ったか（infra_events.residual_resources）。
--    Tier B は Time Travel / Fail-safe / カタログ内オブジェクト / stage 成果物が残留候補。
--    findings の中身（severity / kind）は scripts/check_residual.py の記録形式。
select platform,
       created_at,
       status,
       duration_seconds,
       jsonb_array_length(coalesce(residual_resources -> 'findings', '[]'::jsonb)) as finding_count,
       residual_resources
from baseline_infra_events
where action = 'destroy'
order by platform, created_at desc;

-- 補助: Neon 到達経路の内訳（direct / collected）
--    到達できなかったこと自体が結果なので、必ずレポートの一行にする。
--    Tier A が direct・Tier B が collected に寄る、が事前仮説（実測で検証）。
select platform,
       tier,
       write_path,
       count(*) as runs
from baseline_runs
where params ->> 'action' is distinct from 'teardown'
group by platform, tier, write_path
order by platform, write_path;
