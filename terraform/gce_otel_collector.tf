# Otel Collector（ログパイプラインのみ）とデバイスシミュレーターを同居させるGCEインスタンス。
# シミュレーターをここで動かすのは、VPC内からCloud SQLのプライベートIPに直接届き、常駐プロセスに
# 向くため（docs/design.md 7.2節）。
#
# 起動スクリプトはブート時にのみ実行される。シミュレーターのコードや設定を変えて apply した場合は
# メタデータが更新されるだけなので、反映するにはインスタンス上で
#   sudo google_metadata_script_runner startup
# を実行するか、インスタンスをリセットする（terraform/README.md 参照）。

locals {
  device_simulator_dir = "${path.module}/../device_simulator"

  # シミュレーター一式（tests を除く .py と systemd unit）をパス→内容のJSONにしてメタデータで渡す。
  # 起動スクリプトが /opt/device-simulator/device_simulator/ 以下に展開する。
  device_simulator_bundle = jsonencode(merge(
    { for f in fileset(local.device_simulator_dir, "*.py") : "device_simulator/${f}" => file("${local.device_simulator_dir}/${f}") },
    { "device-simulator.service" = file("${local.device_simulator_dir}/device-simulator.service") },
  ))

  # /etc/device-simulator/env に書き出す環境変数（DBパスワードは含めず、シミュレーターが起動時に
  # Secret Managerから取得する）
  device_simulator_env = join("\n", [
    "DB_HOST=${google_sql_database_instance.postgres.private_ip_address}",
    "DB_NAME=${var.db_name}",
    "DB_USER=${var.db_user}",
    "DB_PASSWORD_SECRET=${google_secret_manager_secret.db_password.secret_id}",
    "SIM_SEND_INTERVAL_SECONDS=${var.sim_send_interval_seconds}",
    "SIM_GROUP_COUNT=${var.sim_group_count}",
    "SIM_ROLE_COUNTS=${var.sim_role_counts}",
    "SIM_RETENTION_HOURS=${var.sim_retention_hours}",
  ])
}

resource "google_compute_instance" "otel_collector" {
  name         = "otel-collector-${var.environment}"
  machine_type = var.gce_machine_type
  zone         = var.zone
  tags         = ["otel-collector"]

  # 起動時にPub/Subの購読を開始するため、subscription・IAM権限が先に存在している必要がある
  depends_on = [
    google_project_service.apis,
    google_pubsub_subscription.otel_collector_logs,
    google_pubsub_subscription_iam_member.gce_otel_subscriber,
    google_secret_manager_secret_iam_member.gce_simulator_db_password_access,
    google_sql_user.app_user,
  ]

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 20
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.subnet.id
    # 外部IPは付与しない（Cloud NAT経由で外部通信、IAP経由でSSH）
  }

  service_account {
    email  = google_service_account.gce_otel_sa.email
    scopes = ["cloud-platform"]
  }

  metadata = {
    environment                 = var.environment
    grafana_cloud_otlp_endpoint = var.grafana_cloud_otlp_endpoint
    grafana_cloud_instance_id   = var.grafana_cloud_instance_id
    # Grafana Cloud APIキーはSecret Manager経由で起動スクリプト内から取得する（メタデータには載せない）。

    # otel-collector/otel-collector-config.yaml をレンダリングした内容。
    # APIキーは埋め込まず __GRAFANA_CLOUD_API_KEY__ のプレースホルダのまま残るため、
    # このメタデータ自体に機密情報は含まれない（起動スクリプト側で置換する）。
    otel_collector_config = templatefile("${path.module}/../otel-collector/otel-collector-config.yaml", {
      project_id                  = var.project_id
      environment                 = var.environment
      grafana_cloud_otlp_endpoint = var.grafana_cloud_otlp_endpoint
      grafana_cloud_instance_id   = var.grafana_cloud_instance_id
    })

    # デバイスシミュレーター（docs/design.md 7章）
    enable_device_simulator = tostring(var.enable_device_simulator)
    device_simulator_bundle = local.device_simulator_bundle
    device_simulator_env    = local.device_simulator_env
  }

  # Otel Collector(contrib)のインストール・設定、デバイスシミュレーターの配置・起動を行う。
  # Terraform側の${...}補間とbashの変数展開が衝突しないよう、file()でそのまま読み込み、
  # テンプレート処理は行っていない。
  metadata_startup_script = file("${path.module}/scripts/otel_collector_startup.sh")
}
