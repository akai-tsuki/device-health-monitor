resource "google_artifact_registry_repository" "check_job_repo" {
  location      = var.region
  repository_id = "check-job-${var.environment}"
  format        = "DOCKER"
  description   = "チェック処理（Cloud Run Job）のコンテナイメージ格納用"

  depends_on = [google_project_service.apis]
}
