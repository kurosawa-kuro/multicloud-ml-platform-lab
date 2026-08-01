"""外部マネージドサービスごとの実装をまとめる名前空間。

トップレベルに vertex / sagemaker / ... を並べると2つ問題が出るため、
ここへ寄せている:

1. **SDK との名前空間衝突**
   `snowflake-connector-python` は `snowflake`、`databricks-sdk` は `databricks`
   というトップレベル名を使う。`PYTHONPATH=src` の下に同名パッケージを置くと
   SDK の import が壊れる（`No module named 'snowflake.connector'`）。
   1階層下げることで衝突しない。
   同じ理由で `platform`（Python 標準ライブラリ）も名前として使えないため複数形。

2. **src/ 直下の責務が読めなくなる**
   common / serving / telemetry（役割）と vertex / sagemaker（サービス名）が
   同列に並ぶと、粒度の違うものが混ざる。

構成（infra/modules/ と1対1で対応させている）:

    vertex / sagemaker / azureml    Tier A・比較対象
    databricks / snowflake          Tier B・比較対象
    neon                            比較対象ではなく **全基盤の計測到達点**。
                                    接続層は src/core/telemetry も使う共有部品

adapter が満たす契約は contracts.ports.PlatformAdapter。neon だけはこの契約を
実装しない（学習ジョブを投げる先ではないため）。
"""
