---
paths:
  - "**/*.tf"
  - "**/*.tfvars"
  - "terraform/**"
  - "infra/**"
---

# Terraform / IaC ルール

- **plan-first**。`terraform plan` の差分を人が読み、承認を得てから `apply` する。無確認 apply はしない。
- `apply` / `destroy` は Heavy・保護 capability（`classify-task` で必ず Heavy）。owner approval 前提。`destroy` は settings.json で deny 既定。
- `*.tfstate` / `*.tfvars` / `.terraform/` は**絶対にコミットしない**（.gitignore 済み）。state に含まれる値を docs / logs に貼らない。
- 変更は最小差分。既存リソースの置換（destroy→create）になる変更は plan で置換記号を確認し、影響を task に明記する。
- `infra/**` / `terraform/**` は `detect-safety-boundary` hook の保護パス。編集は owner 確認境界。
