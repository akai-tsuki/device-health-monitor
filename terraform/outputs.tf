output "cloud_sql_private_ip" {
  description = "Cloud SQL(Postgres)のプライベートIPアドレス"
  value       = google_sql_database_instance.postgres.private_ip_address
}

output "cloud_sql_instance_connection_name" {
  description = "Cloud SQL接続名（Cloud SQL Auth Proxy等で使用）"
  value       = google_sql_database_instance.postgres.connection_name
}

output "artifact_registry_repo_url" {
  description = "Cloud Run Job用コンテナイメージのpush先"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.check_job_repo.repository_id}"
}

output "cloud_run_job_name" {
  description = "作成したCloud Run Job名"
  value       = google_cloud_run_v2_job.check_job.name
}

output "cloud_scheduler_job_name" {
  description = "作成したCloud Schedulerジョブ名"
  value       = google_cloud_scheduler_job.check_trigger.name
}

output "otel_collector_instance_name" {
  description = "Otel Collector / デバイスシミュレーターを稼働させるGCEインスタンス名"
  value       = google_compute_instance.otel_collector.name
}

output "vpc_network_name" {
  description = "作成したVPCネットワーク名"
  value       = google_compute_network.vpc.name
}

output "check_job_logs_topic" {
  description = "Cloud Run JobのログをLog Router sinkが転送するPub/Subトピック名"
  value       = google_pubsub_topic.check_job_logs.name
}

output "otel_collector_logs_subscription" {
  description = "Otel Collectorがログを購読するPub/Subサブスクリプション名"
  value       = google_pubsub_subscription.otel_collector_logs.name
}

output "grafana_dashboard_url" {
  description = "Grafana DashboardのURL"
  value       = grafana_dashboard.device_health.url
}
