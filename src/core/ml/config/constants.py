"""ML コードの定数。

流用元: private-ops starter-kit `housing_ml/constants.py`。
**変更点**: 入力データ契約（列名・seed）を統合した。
計測スキーマ（ml_runs 等）は関心が違うので core/telemetry/schemas.py。
"""

MODEL_NAME = "lightgbm-california-housing"
# 配布の起点は Neon のテーブル（sklearn 直取得ではない）
DATASET_NAME = "neon.mlcompare.california_housing"

# --- 入力データ契約 -------------------------------------------------------
#
# 列名は snake_case を正とする。sklearn 由来の CamelCase（MedInc 等）ではなく
# 配布の起点である Neon のテーブル列名に揃えている。
#
# 理由: Snowflake は識別子を大文字に畳み、Databricks / SQL 系も snake_case が自然。
# CamelCase を正にすると5基盤すべてで変換が要る。ここを1度決めておけば、
# 各基盤側の正規化は「大文字小文字を揃える」だけで済む。

TARGET_COLUMN = "med_house_val"

FEATURE_COLUMNS: tuple[str, ...] = (
    "med_inc",
    "house_age",
    "ave_rooms",
    "ave_bedrms",
    "population",
    "ave_occup",
    "latitude",
    "longitude",
)

RANDOM_SEED = 42
