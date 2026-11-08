resource "google_cloud_scheduler_job" "check_trigger" {
  name      = "check-trigger-${var.environment}"
  region    = var.region
  schedule  = "*/${var.check_interval_minutes} * * * *" # 例: 5分毎 -> "*/5 * * * *"
  time_zone = var.system_timezone

  depends_on = [google_project_service.apis]

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.check_job.name}:run"

    oauth_token {
      service_account_email = google_service_account.scheduler_sa.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  retry_config {
    retry_count = 1
  }
}
