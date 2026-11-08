# check_job/ のイメージ（check_job/Dockerfile）をArtifact Registryへpushしてから
# apply すること。イメージが未pushの状態でこのimageを指定すると、Cloud Run Jobの作成/更新に失敗する。
# 手順は terraform/README.md「構築手順」を参照。
#   docker build -f check_job/Dockerfile -t check-job .
#   docker tag check-job ${region}-docker.pkg.dev/${project_id}/check-job-${environment}/check-job:latest
#   docker push ${region}-docker.pkg.dev/${project_id}/check-job-${environment}/check-job:latest

resource "google_cloud_run_v2_job" "check_job" {
  name     = "check-job-${var.environment}"
  location = var.region

  depends_on = [google_project_service.apis]

  template {
    template {
      service_account = google_service_account.cloud_run_job_sa.email

      # Cloud SQL(プライベートIP)へ到達するため、VPCへ直接接続する
      vpc_access {
        network_interfaces {
          network    = google_compute_network.vpc.id
          subnetwork = google_compute_subnetwork.subnet.id
        }
        egress = "PRIVATE_RANGES_ONLY"
      }

      containers {
        image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.check_job_repo.repository_id}/check-job:latest"

        env {
          name  = "DB_HOST"
          value = google_sql_database_instance.postgres.private_ip_address
        }
        env {
          name  = "DB_NAME"
          value = var.db_name
        }
        env {
          name  = "DB_USER"
          value = var.db_user
        }
        env {
          name = "DB_PASSWORD"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.db_password.secret_id
              version = "latest"
            }
          }
        }
        env {
          name  = "ENV"
          value = var.environment
        }
        env {
          name  = "TZ_NAME"
          value = var.system_timezone
        }
        env {
          name  = "CHECK_INTERVAL_MINUTES"
          value = tostring(var.check_interval_minutes)
        }
        env {
          name  = "MISSING_THRESHOLD_SECONDS"
          value = tostring(var.missing_threshold_seconds)
        }
        env {
          name  = "DELAY_THRESHOLD_SECONDS"
          value = tostring(var.delay_threshold_seconds)
        }
        # 一時ロストの集計時間帯はチェック間隔と同じ値にする（docs/design.md 2章）
        env {
          name  = "LOST_WINDOW_MINUTES"
          value = tostring(var.check_interval_minutes)
        }
        env {
          name  = "LOST_MAX_RECORDS"
          value = tostring(var.lost_max_records)
        }
        env {
          name  = "LOW_BATTERY_THRESHOLD_VOLTAGE"
          value = var.low_battery_threshold_voltage
        }
      }

      # リトライすると同じ run_id（実行名）でログが二重に出るため、リトライしない。
      # 失敗は check_result（success=false）または check_result の途絶としてアラートで検知する。
      max_retries = 0
      timeout     = "300s"
    }
  }
}
