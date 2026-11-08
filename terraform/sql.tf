resource "random_password" "db_password" {
  length  = 24
  special = false
}

resource "google_sql_database_instance" "postgres" {
  name             = "pg-${var.environment}"
  database_version = "POSTGRES_15"
  region           = var.region

  depends_on = [
    google_project_service.apis,
    google_service_networking_connection.private_vpc_connection,
  ]

  settings {
    tier = var.db_tier

    ip_configuration {
      ipv4_enabled    = false # 検証環境のためパブリックIPは無効化し、プライベート接続のみとする
      private_network = google_compute_network.vpc.id
    }

    backup_configuration {
      enabled = true
    }
  }

  # 検証環境のため、destroy時に削除保護を無効化（本番移行時はtrueに変更すること）
  deletion_protection = false
}

resource "google_sql_database" "app_db" {
  name     = var.db_name
  instance = google_sql_database_instance.postgres.name
}

resource "google_sql_user" "app_user" {
  name     = var.db_user
  instance = google_sql_database_instance.postgres.name
  password = random_password.db_password.result
}
