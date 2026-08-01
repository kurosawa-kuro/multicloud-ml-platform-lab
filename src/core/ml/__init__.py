"""5基盤共通の ML コード。

このパッケージの git SHA（`code_revision`）が比較成立の担保。
依存は lightgbm / scikit-learn / pandas / pyarrow のみに保つ
（Snowflake warehouse の Anaconda channel 制約と
Databricks ML Runtime のプリインストール衝突を避けるため）。

クラウド SDK をこの階層から import しない。基盤依存は src/<platform>/ に閉じる。
"""
