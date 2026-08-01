# Neon 用: project / branch / database / role（全基盤の計測到達点。他5基盤とは独立に管理する）

variable "project_name" {
  description = "リソース名の接頭辞。命名は locals で一元化する"
  type        = string
}

variable "environment" {
  description = "dev / stg / prd"
  type        = string
  default     = "dev"
}

# TODO(Phase): 基盤固有の変数
