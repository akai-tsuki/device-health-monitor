terraform {
  required_version = ">= 1.5.0"

  # state はリポジトリ外に置く（DBパスワード等を含むため）。保存先は init 時に指定する：
  #   terraform init -backend-config="path=$HOME/.dhm/dev.tfstate"
  # 本番ではGCS等のリモートバックエンドへの移行を推奨。
  backend "local" {}

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.30"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    grafana = {
      source  = "grafana/grafana"
      version = "~> 3.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

# Alert rule / DashboardをTerraformで管理するためのGrafana Cloud接続。
# url は対象スタックのURL、auth はそのスタック内で発行したService Account Token
# （Grafana Cloudの組織レベルAccess Policyトークンではない）。
provider "grafana" {
  url  = var.grafana_cloud_url
  auth = var.grafana_sa_token
}

# ------------------------------------------------------------
# 利用するGCP APIを有効化
# 旧リポジトリから monitoring.googleapis.com を削除（チェック結果はCloud Loggingのみに出力する）。
# ------------------------------------------------------------
locals {
  required_apis = [
    "compute.googleapis.com",
    "sqladmin.googleapis.com",
    "servicenetworking.googleapis.com",
    "run.googleapis.com",
    "cloudscheduler.googleapis.com",
    "secretmanager.googleapis.com",
    "logging.googleapis.com",
    "artifactregistry.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "pubsub.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each = toset(local.required_apis)

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}
