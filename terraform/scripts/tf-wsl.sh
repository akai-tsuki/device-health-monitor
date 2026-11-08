#!/bin/bash
# WSL から Terraform を実行するためのラッパー。
#
# Windows 側（/mnt/c）のリポジトリでは、Terraform がプロバイダやロックファイルの権限を設定できず
# init に失敗する。また state（DBパスワード等を含む）や秘密情報をリポジトリ内に置かないため、
# 次の構成で実行する。
#   - リポジトリの terraform/ otel-collector/ device_simulator/ を ~/.dhm/work-<env>/ に同期して実行
#   - state:          ~/.dhm/<env>.tfstate
#   - プロバイダ等:   ~/.dhm/tfdata-<env>/（TF_DATA_DIR）
#   - 秘密情報:       ~/.dhm/<env>-secrets.tfvars（plan / apply / destroy に自動で -var-file を付ける）
#
# 使い方（WSL で、リポジトリのどこからでも）:
#   terraform/scripts/tf-wsl.sh init
#   terraform/scripts/tf-wsl.sh plan
#   terraform/scripts/tf-wsl.sh apply
#   terraform/scripts/tf-wsl.sh output
# 環境名は DHM_ENV（デフォルト dev）。
set -eu -o pipefail

ENV_NAME=${DHM_ENV:-dev}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
BASE=$HOME/.dhm
WORK=$BASE/work-$ENV_NAME
SECRETS=$BASE/$ENV_NAME-secrets.tfvars
export TF_DATA_DIR=$BASE/tfdata-$ENV_NAME

mkdir -p "$WORK" "$TF_DATA_DIR"
chmod 700 "$BASE"
for d in terraform otel-collector device_simulator; do
  rsync -a --delete --exclude '.terraform' --exclude '.terraform.lock.hcl' \
    --exclude '__pycache__' --exclude 'tests' "$REPO/$d/" "$WORK/$d/"
done
cd "$WORK/terraform"

# ロックファイルは同期で消えないよう TF_DATA_DIR 側に保存しておく
[ -f "$TF_DATA_DIR/lock.hcl" ] && cp "$TF_DATA_DIR/lock.hcl" .terraform.lock.hcl

cmd=${1:-}
case "$cmd" in
  init)
    shift
    terraform init -reconfigure -backend-config="path=$BASE/$ENV_NAME.tfstate" "$@"
    ;;
  plan|apply|destroy|console|import|refresh)
    shift
    [ -f "$SECRETS" ] || { echo "秘密情報ファイルがありません: $SECRETS" >&2; exit 1; }
    terraform "$cmd" -var-file="$SECRETS" "$@"
    ;;
  *)
    terraform "$@"
    ;;
esac

[ -f .terraform.lock.hcl ] && cp .terraform.lock.hcl "$TF_DATA_DIR/lock.hcl"
