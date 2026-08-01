# Neon 用: project / branch / database / role（全基盤の計測到達点。他5基盤とは独立に管理する）
#
# Terraform provider を使わずコンソール/API で作る選択もある。どちらにしたかを比較レポートに記録する
#
# 境界の原則（docs/02_architecture.md「境界」）:
#   静的基盤 = Terraform / ジョブ実行・登録・デプロイ = SDK・CLI・SQL
#   terraform apply に学習実行を含めない。state に ML 実行履歴が混ざると
#   インフラ状態と実行履歴の両方の再現性が落ちる。
#
# 「Terraform でどこまで書けたか」自体が比較軸なので、
# 書けなかったもの・SDK に逃がしたものは docs/comparison/ に必ず残す。

# TODO(Phase): リソース定義
