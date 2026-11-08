variable "project_id" {
  description = "GCPプロジェクトID"
  type        = string
}

variable "region" {
  description = "リソースを配置するリージョン"
  type        = string
  default     = "asia-northeast1"
}

variable "zone" {
  description = "GCEインスタンスを配置するゾーン"
  type        = string
  default     = "asia-northeast1-a"
}

variable "environment" {
  description = "環境名（リソース名のサフィックス、ログの env フィールドに使用）。旧環境の validation とは別の値にすること（削除した Cloud SQL インスタンス名は約1週間再利用できないため）"
  type        = string
  default     = "dev"
}

variable "system_timezone" {
  description = "システム全体で使うタイムゾーン（IANA名）。ログの日時表記（TZ_NAME）、Grafana Dashboardの表示、Cloud Schedulerのcron解釈に使う"
  type        = string
  default     = "Asia/Tokyo"
}

# --------------------------------------------------------------------------
# Cloud SQL
# --------------------------------------------------------------------------
variable "db_tier" {
  description = "Cloud SQLのマシンタイプ（検証用に小さめのデフォルト）"
  type        = string
  default     = "db-custom-1-3840"
}

variable "db_name" {
  description = "作成するデータベース名"
  type        = string
  default     = "appdb"
}

variable "db_user" {
  description = "アプリケーション用DBユーザー名"
  type        = string
  default     = "app_user"
}

# --------------------------------------------------------------------------
# チェック処理（Cloud Run Jobへ環境変数として渡す。docs/design.md 2章）
# --------------------------------------------------------------------------
variable "check_interval_minutes" {
  description = "Cloud Schedulerによるチェック実行間隔（分）。checked_at の切り捨て単位・遅延/一時ロストの判定時間帯にも使う。60の約数であること"
  type        = number
  default     = 5

  validation {
    condition     = var.check_interval_minutes >= 1 && 60 % var.check_interval_minutes == 0
    error_message = "check_interval_minutes は60の約数にしてください。"
  }
}

variable "missing_threshold_seconds" {
  description = "データ欠損の閾値（秒）。最終登録からこの秒数を超えたら missing"
  type        = number
  default     = 900
}

variable "delay_threshold_seconds" {
  description = "データ遅延の閾値（秒）。registered_at - processed_at がこの秒数以上のレコードがあれば delay"
  type        = number
  default     = 20
}

variable "lost_max_records" {
  description = "一時ロストの閾値（件）。判定時間帯のレコード数がこの件数以下なら lost"
  type        = number
  default     = 8
}

variable "low_battery_threshold_voltage" {
  description = "バッテリー低下の閾値（V）。最新電圧がこの値未満なら low_battery"
  type        = string
  default     = "5.00"
}

# --------------------------------------------------------------------------
# GCE（Otel Collector + デバイスシミュレーター）
# --------------------------------------------------------------------------
variable "gce_machine_type" {
  description = "Otel Collector / デバイスシミュレーターをのせるGCEインスタンスのマシンタイプ"
  type        = string
  default     = "e2-small"
}

# --------------------------------------------------------------------------
# デバイスシミュレーター（docs/design.md 7章。検証・デモ専用）
# --------------------------------------------------------------------------
variable "enable_device_simulator" {
  description = "GCE上でデバイスシミュレーター（device-simulator.service）を稼働させるか"
  type        = bool
  default     = true
}

variable "sim_send_interval_seconds" {
  description = "シミュレーターの送信間隔（秒）"
  type        = number
  default     = 10
}

variable "sim_group_count" {
  description = "シミュレーターが作成するグループ数（demo-group-1 〜 N）"
  type        = number
  default     = 3
}

variable "sim_role_counts" {
  description = "1グループあたりの役割別デバイス数（role=件数 のカンマ区切り。役割: normal / delay / lost / missing / never）"
  type        = string
  default     = "normal=3,delay=1,lost=1,missing=1,never=1"
}

variable "sim_retention_hours" {
  description = "シミュレーターが残すデモ用レコードの保持時間（これより古いレコードを1時間ごとに削除）"
  type        = number
  default     = 24
}

# --------------------------------------------------------------------------
# Grafana Cloud（OTLP送信）
# --------------------------------------------------------------------------
variable "grafana_cloud_otlp_endpoint" {
  description = "Grafana CloudのOTLPエンドポイント（例: https://otlp-gateway-prod-ap-northeast-0.grafana.net/otlp）。Grafana Cloudのポータル > Configuration > OpenTelemetry で確認できる"
  type        = string
}

variable "grafana_cloud_instance_id" {
  description = "Grafana CloudのInstance ID（OTLP認証のBasic認証ユーザー名として使用）"
  type        = string
}

variable "grafana_cloud_api_key" {
  description = "Grafana CloudのAPIキー/Access Policy Token（OTLP送信用。Secret Managerに格納。terraform.tfvarsで指定し、Gitにはコミットしないこと）"
  type        = string
  sensitive   = true
}

# --------------------------------------------------------------------------
# Grafana Cloud Alert rule / Dashboard（grafanaプロバイダで管理）
# --------------------------------------------------------------------------
variable "grafana_cloud_url" {
  description = "Grafana CloudスタックのURL（例: https://xxxxx.grafana.net）"
  type        = string
}

variable "grafana_sa_token" {
  description = "Grafana Cloudスタック内で発行したService Account Token（Admin権限。Dashboard/Alert rule管理用。Gitにはコミットしないこと）"
  type        = string
  sensitive   = true
}

variable "grafana_loki_datasource_name" {
  description = "チェックアプリのログが格納されているGrafana内Lokiデータソース名（例: grafanacloud-xxxxx-logs）"
  type        = string
}

variable "grafana_loki_stream_selector" {
  description = "チェックアプリのログを選択するLokiのストリームセレクタ。空文字なら {service_name=\"check-job-<environment>\"}（Otel Collector が service.name を付与する。docs/grafana-implementation-notes.md）"
  type        = string
  default     = ""
}

variable "alert_notification_email" {
  description = "アラートの通知先メールアドレス。空文字の場合は contact point を作らず、アラートは Grafana の通知ポリシー（既定）に従う"
  type        = string
  default     = ""
}
