# ------------------------------------------------------------
# Cloud Run Job用サービスアカウント
#   - Cloud SQLへの接続
#   - Cloud Loggingへのログ書き込み（Cloud Runは標準出力を自動送信するため明示権限は不要だが、
#     将来Logging APIを直接呼ぶ場合に備えて付与）
#   - Secret Managerからのシークレット取得
# 旧リポジトリの roles/monitoring.metricWriter は削除（Cloud Monitoringへは出力しない）。
# ------------------------------------------------------------
resource "google_service_account" "cloud_run_job_sa" {
  account_id   = "check-job-sa-${var.environment}"
  display_name = "Cloud Run Job (check process) service account"
}

resource "google_project_iam_member" "cloud_run_job_sql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.cloud_run_job_sa.email}"
}

resource "google_project_iam_member" "cloud_run_job_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.cloud_run_job_sa.email}"
}

resource "google_secret_manager_secret_iam_member" "cloud_run_job_db_password_access" {
  secret_id = google_secret_manager_secret.db_password.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloud_run_job_sa.email}"
}

# ------------------------------------------------------------
# GCE（Otel Collector + デバイスシミュレーター）用サービスアカウント
#   - ログは Log Router sink 経由で Pub/Sub に転送されたものを購読する（pubsub_logging.tf）
#   - Grafana Cloud APIキー（OTLP送信用）の取得
#   - デバイスシミュレーター有効時のみ、DBパスワードの取得
# 旧リポジトリの roles/monitoring.viewer は削除（googlecloudmonitoring receiver を使わない）。
# ------------------------------------------------------------
resource "google_service_account" "gce_otel_sa" {
  account_id   = "otel-collector-sa-${var.environment}"
  display_name = "GCE Otel Collector / device simulator service account"
}

resource "google_secret_manager_secret_iam_member" "gce_otel_grafana_key_access" {
  secret_id = google_secret_manager_secret.grafana_cloud_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.gce_otel_sa.email}"
}

resource "google_secret_manager_secret_iam_member" "gce_simulator_db_password_access" {
  count = var.enable_device_simulator ? 1 : 0

  secret_id = google_secret_manager_secret.db_password.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.gce_otel_sa.email}"
}

# ------------------------------------------------------------
# Cloud Scheduler用サービスアカウント
#   - Cloud Run Jobの実行（run.jobs.run）権限のみを付与
# ------------------------------------------------------------
resource "google_service_account" "scheduler_sa" {
  account_id   = "scheduler-sa-${var.environment}"
  display_name = "Cloud Scheduler service account (invoke check job)"
}

resource "google_cloud_run_v2_job_iam_member" "scheduler_can_invoke" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.check_job.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler_sa.email}"
}
