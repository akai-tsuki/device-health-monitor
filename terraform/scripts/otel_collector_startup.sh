#!/bin/bash
# GCE起動スクリプト。
#   1. Otel Collector(contrib)をインストールし、Secret ManagerからGrafana Cloud APIキーを取得して
#      設定ファイルに埋め込んだ上で起動する。
#   2. enable_device_simulator=true の場合、デバイスシミュレーターを配置し systemd サービスとして起動する。
#      false の場合は、既に配置済みならサービスを停止・無効化する。
#
# Terraformのmetadata_startup_scriptにはfile()でそのまま渡しており、Terraform側の
# ${...}補間は一切行っていない（bashの変数展開とTerraformの補間構文が衝突するのを避けるため）。
# 必要な値はすべてインスタンスメタデータ経由で実行時に取得する。ブートのたびに実行されるため冪等にしている。
set -eu -o pipefail

MD="http://metadata.google.internal/computeMetadata/v1"
md() { curl -sf -H "Metadata-Flavor: Google" "${MD}/$1"; }

apt-get update -y
apt-get install -y curl jq

# --- メタデータから設定値を取得 ---
PROJECT_ID=$(md project/project-id)
ENVIRONMENT=$(md instance/attributes/environment)

# ============================================================
# 1. Otel Collector
# ============================================================
if ! command -v otelcol-contrib >/dev/null 2>&1; then
  # 最新安定版をインストール
  LATEST_URL=$(curl -sL -o /dev/null -w '%{url_effective}' \
    "https://github.com/open-telemetry/opentelemetry-collector-releases/releases/latest")
  VERSION=$(basename "$LATEST_URL" | sed 's/^v//')

  curl -sL -o /tmp/otelcol-contrib.deb \
    "https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v${VERSION}/otelcol-contrib_${VERSION}_linux_amd64.deb"
  dpkg -i /tmp/otelcol-contrib.deb || apt-get install -f -y
fi

# project_id / environment / grafana_cloud_otlp_endpoint / grafana_cloud_instance_id は
# Terraform（templatefile）側で埋め込み済み。APIキーだけはプレースホルダのままなので、
# ここでSecret Managerから取得して置換する。
md instance/attributes/otel_collector_config > /etc/otelcol-contrib/config.yaml

ACCESS_TOKEN=$(md instance/service-accounts/default/token | jq -r .access_token)

GRAFANA_API_KEY=$(curl -sf \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  "https://secretmanager.googleapis.com/v1/projects/${PROJECT_ID}/secrets/grafana-cloud-api-key-${ENVIRONMENT}/versions/latest:access" \
  | jq -r .payload.data | base64 -d)

sed -i "s|__GRAFANA_CLOUD_API_KEY__|${GRAFANA_API_KEY}|" /etc/otelcol-contrib/config.yaml

# otelcol-contribサービスはroot以外の専用ユーザーで動作するため、そのユーザーが読める
# 所有者・権限に設定する（サービスに設定済みのUser/Groupを動的に取得して合わせる）。
SERVICE_USER=$(systemctl show otelcol-contrib -p User --value)
SERVICE_GROUP=$(systemctl show otelcol-contrib -p Group --value)
chown "${SERVICE_USER:-root}:${SERVICE_GROUP:-root}" /etc/otelcol-contrib/config.yaml
chmod 640 /etc/otelcol-contrib/config.yaml

systemctl enable otelcol-contrib
systemctl restart otelcol-contrib

# ============================================================
# 2. デバイスシミュレーター（docs/design.md 7章）
# ============================================================
ENABLE_SIM=$(md instance/attributes/enable_device_simulator || echo false)
SIM_DIR=/opt/device-simulator
UNIT=/etc/systemd/system/device-simulator.service

if [ "${ENABLE_SIM}" = "true" ]; then
  apt-get install -y python3 python3-psycopg2

  # コード一式（パス→内容のJSON）を展開する
  BUNDLE=$(mktemp)
  md instance/attributes/device_simulator_bundle > "${BUNDLE}"
  rm -rf "${SIM_DIR}/device_simulator"
  mkdir -p "${SIM_DIR}"
  jq -r 'keys[]' "${BUNDLE}" | while read -r path; do
    mkdir -p "$(dirname "${SIM_DIR}/${path}")"
    jq -j --arg p "${path}" '.[$p]' "${BUNDLE}" > "${SIM_DIR}/${path}"
  done
  rm -f "${BUNDLE}"
  mv "${SIM_DIR}/device-simulator.service" "${UNIT}"
  chmod -R a+rX "${SIM_DIR}"

  # 環境変数ファイル（DBパスワードは含まない。シミュレーターが Secret Manager から取得する）
  mkdir -p /etc/device-simulator
  md instance/attributes/device_simulator_env > /etc/device-simulator/env
  chmod 600 /etc/device-simulator/env

  systemctl daemon-reload
  systemctl enable device-simulator
  systemctl restart device-simulator
elif [ -f "${UNIT}" ]; then
  systemctl disable --now device-simulator || true
fi
