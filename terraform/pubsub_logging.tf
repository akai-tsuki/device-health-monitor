# Cloud Run Jobのログを Otel Collector（GCE）へ届けるための経路。
#
# 当初は googlecloudlogging という直接ポーリング型のreceiverを想定していたが、
# opentelemetry-collector-contribに実在しないコンポーネントだったため、
# 現行のcontribが提供する構成（Log Router sink → Pub/Sub → googlecloudpubsub receiver）に
# 修正した（旧リポジトリでの経緯）。詳細: otel-collector/README.md。

resource "google_pubsub_topic" "check_job_logs" {
  name = "check-job-logs-${var.environment}"

  depends_on = [google_project_service.apis]
}

resource "google_pubsub_subscription" "otel_collector_logs" {
  name  = "otel-collector-logs-${var.environment}"
  topic = google_pubsub_topic.check_job_logs.id
}

# Cloud Run Jobのログのみを対象にPub/Subへルーティングするシンク。
resource "google_logging_project_sink" "check_job_logs_sink" {
  name        = "check-job-logs-sink-${var.environment}"
  destination = "pubsub.googleapis.com/${google_pubsub_topic.check_job_logs.id}"
  filter      = "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"${google_cloud_run_v2_job.check_job.name}\""

  # シンク専用のサービスアカウント（writer_identity）を発行させ、
  # そのアカウントにのみPub/Subへのpublish権限を与える。
  unique_writer_identity = true
}

resource "google_pubsub_topic_iam_member" "logging_sink_publisher" {
  topic  = google_pubsub_topic.check_job_logs.name
  role   = "roles/pubsub.publisher"
  member = google_logging_project_sink.check_job_logs_sink.writer_identity
}

resource "google_pubsub_subscription_iam_member" "gce_otel_subscriber" {
  subscription = google_pubsub_subscription.otel_collector_logs.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_service_account.gce_otel_sa.email}"
}
