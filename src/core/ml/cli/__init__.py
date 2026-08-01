"""実行インタフェース（薄い CLI）。

    python -m core.ml.cli --input DIR --output DIR --params JSON

学習ロジックは core.ml.training、編成は core.ml.pipelines が持つ。
ここには argparse と exit code 規約しか置かない。
"""
