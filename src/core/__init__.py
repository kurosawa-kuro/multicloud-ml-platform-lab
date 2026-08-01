"""自前実装のうち、5基盤すべてへ同一のものが配布される中身。

    ml/         California Housing の学習コード。ここの git SHA が `code_revision`
                であり、比較が成立していることの担保そのもの
    app/        Tier A の推論アプリ（1イメージで3契約）
    telemetry/  計測データの Neon 書き込み

src/platforms/ との境界:

    core/       自前で書くもの。5基盤で同一に保つ（差があってはいけない）
    platforms/  外部サービスに合わせるもの。5基盤で異なる（差そのものが計測対象）

この境界が崩れると比較が無効になる。core/ の中でクラウド SDK を import したり、
プラットフォーム名で分岐したりしない。差は platforms/ 側の adapter に閉じる。

依存は最小に保つ（lightgbm / scikit-learn / pandas / pyarrow / psycopg）。
Snowflake warehouse は Anaconda channel 限定でバージョン固定が効きにくく、
Databricks は ML Runtime プリインストール版と wheel 依存が衝突しうるため。
"""
