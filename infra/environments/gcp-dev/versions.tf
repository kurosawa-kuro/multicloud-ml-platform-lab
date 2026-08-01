terraform {
  required_version = ">= 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region

  # ADC（`gcloud auth application-default login`）で apply するとき、
  # **billingbudgets API は quota project を要求する**。既定では設定されないため
  # 予算アラートの作成だけが 403 で落ちる（2026-08-02 実測。修正09 で予算を必須化した
  # 直後の再構築で踏んだ）:
  #
  #   Error 403: Your application is authenticating by using local Application Default
  #   Credentials. The billingbudgets.googleapis.com API requires a quota project ...
  #
  # `gcloud auth application-default set-quota-project` だけでは**足りない**（実測）。
  # provider 側で user_project_override を立て、billing_project を明示する必要がある。
  # 環境変数（USER_PROJECT_OVERRIDE / GOOGLE_BILLING_PROJECT）でも通るが、
  # **それは手順書に書く回避策であって設定ではない** —— 知らない人が apply すると
  # 同じ 403 を踏む。ガードレール（予算）が「黙って作られない」ことこそ修正09 が
  # 潰した欠陥なので、ここに固定する。
  user_project_override = true
  billing_project       = var.project_id
}
