# デバイスデータ監視 検証環境 Terraform

デバイスデータ監視システム（[`../docs/design.md`](../docs/design.md)）の検証環境一式を構築する
Terraformコードです。旧リポジトリ（data-update-monitor）の構成を流用し、Cloud Monitoring関連
（カスタムメトリクス、`googlecloudmonitoring` receiver、Mimir データソース）を削除しています。

## 構築されるリソース

- VPC / サブネット / Cloud NAT / ファイアウォール（`network.tf`）
- Cloud SQL for PostgreSQL（プライベートIPのみ、`sql.tf`）
- Secret Manager（DBパスワード、Grafana Cloud APIキー、`secrets.tf`）
- Artifact Registry（Cloud Run Jobのイメージ格納、`artifact_registry.tf`）
- Cloud Run Job（`check_job`：4種類のチェック、`cloud_run_job.tf`）＋ Cloud Scheduler（`scheduler.tf`）
- Log Router sink ＋ Pub/Sub（Cloud Run Job のログ転送、`pubsub_logging.tf`）
- GCEインスタンス（Otel Collector ＋ デバイスシミュレーター、`gce_otel_collector.tf`）
- Grafana Cloud の Dashboard / Alert rule / Contact point（`grafana` プロバイダ、`grafana.tf`）
- 各種サービスアカウント・IAM権限（`iam.tf`）

## 前提条件

- Terraform >= 1.5.0
- gcloud CLIでの認証済み（`gcloud auth login` と `gcloud auth application-default login`）
- 対象プロジェクトの請求先アカウント設定済み、プロジェクトオーナー相当の権限
- Grafana Cloud スタック内で発行した Service Account Token（Admin権限。Dashboard / Alert rule 管理用。
  Administration > Users and access > Service accounts から作成）
- Grafana Cloud の OTLP 送信用 Access Policy Token（`logs:write` スコープ）

> **注意**：`terraform fmt` は必ず対象ファイルを指定して実行する（引数なしだと `terraform.tfvars` の
> 中身が diff として表示される。[`../docs/grafana-implementation-notes.md`](../docs/grafana-implementation-notes.md)）。

---

## 0. 旧環境（data-update-monitor）の削除

設計書10章の手順1。旧環境はデータ保全不要のため、Grafana 側も含めて `terraform destroy` で削除する。
旧環境の Terraform state は、旧リポジトリをcloneした作業環境（例：WSL の
`~/data-update-monitor/terraform/`）にあるローカル state を使う。

### 0.1 事前確認

```bash
cd ~/data-update-monitor/terraform
terraform state list            # 管理対象（Grafana 側の folder / contact point / rule group / dashboard を含む）
terraform plan -destroy         # 削除対象の確認（この時点では何も削除されない）
```

`plan -destroy` が次のエラーで止まる場合は、それぞれ対処してから再実行する。

| エラー | 原因 | 対処 |
|---|---|---|
| Grafana API が `503` | 無料スタックが休止中 | ブラウザでスタックURLを開く（または `/api/health` にアクセス）と復帰する |
| Grafana API が `401 Expired API key` | `grafana_sa_token` の期限切れ | スタックで Service Account Token を再発行し、旧 `terraform.tfvars` の `grafana_sa_token` を更新 |
| `SQL User ... instance is not running` | Cloud SQL が停止中（`activation-policy=NEVER`） | 下記 0.2 のどちらか |

### 0.2 Cloud SQL が停止している場合

インスタンスを削除すれば DB・ユーザーも消えるため、どちらでもよい。

- **A（推奨）**：DB・ユーザーを state から外し、インスタンスごと削除させる
  ```bash
  terraform state rm google_sql_user.app_user google_sql_database.app_db
  ```
- **B**：インスタンスを起動してから destroy する（起動に数分かかる）
  ```bash
  gcloud sql instances patch pg-validation --activation-policy=ALWAYS --project=<project_id>
  ```

### 0.3 削除

```bash
terraform destroy
```

- `google_service_networking_connection` の削除が「Producers still using this connection」等で失敗した
  場合は、Cloud SQL の削除完了を数分待ってから `terraform destroy` を再実行する。
- API の有効化（`google_project_service`）は `disable_on_destroy = false` のため、無効化されずに残る。
- 削除した Cloud SQL インスタンス名（`pg-validation`）は約1週間再利用できない。新環境は
  `environment = "dev"` 等の別名で作成する（このリポジトリのデフォルトは `dev`）。

### 0.4 削除後の確認

```bash
terraform state list                       # 空になっていること
gcloud sql instances list --project=<project_id>
gcloud run jobs list --region=asia-northeast1 --project=<project_id>
gcloud compute instances list --project=<project_id>
```

Grafana Cloud 側で「データ更新監視」フォルダ（Dashboard / Alert rule）と
`data-update-monitor-email` contact point が消えていることを確認する。

---

## 1. 構築手順

### 1.1 実行環境（WSL）と秘密情報の置き場所

Terraform は WSL（Ubuntu）から [`scripts/tf-wsl.sh`](./scripts/tf-wsl.sh) 経由で実行する。

- Windows 側（`/mnt/c`）のリポジトリでは、Terraform がプロバイダやロックファイルの権限を設定できず
  `init` に失敗する。そのためリポジトリを `~/.dhm/work-dev/` に同期して実行する。
- **state（DBパスワード等を含む）と秘密情報はリポジトリの外**（`~/.dhm/`）に置く。リポジトリ内の
  ファイルは、エディタや AI エージェントなどのツールが差分として読み取ることがあるため。

| ファイル | 内容 |
|---|---|
| `terraform/terraform.tfvars`（リポジトリ内、Git管理外） | 秘密情報**以外**の値（`terraform.tfvars.example` をコピー） |
| `~/.dhm/dev-secrets.tfvars` | `grafana_cloud_api_key` / `grafana_sa_token` |
| `~/.dhm/dev.tfstate` | Terraform state |
| `~/.dhm/tfdata-dev/` | プロバイダ・ロックファイル（`TF_DATA_DIR`） |

秘密情報ファイルは、値を画面に出さずに作る：

```bash
mkdir -p ~/.dhm && chmod 700 ~/.dhm
read -rs T && printf 'grafana_cloud_api_key = "%s"\n' "$T" > ~/.dhm/dev-secrets.tfvars && unset T
read -rs T && printf 'grafana_sa_token = "%s"\n' "$T" >> ~/.dhm/dev-secrets.tfvars && unset T
chmod 600 ~/.dhm/dev-secrets.tfvars
```

### 1.2 構築

Cloud Run Job はイメージが存在しないと作成に失敗するため、先に Artifact Registry を作ってイメージを push する。

```bash
cd /mnt/c/desk/GitHub/device-health-monitor
cp terraform/terraform.tfvars.example terraform/terraform.tfvars   # 秘密情報以外の値を設定

# 1) 初期化と、Artifact Registry・API だけ先に作成
terraform/scripts/tf-wsl.sh init
terraform/scripts/tf-wsl.sh apply -target=google_artifact_registry_repository.check_job_repo

# 2) check_job イメージを build / push（リポジトリルートで実行）
gcloud auth configure-docker asia-northeast1-docker.pkg.dev
docker build -f check_job/Dockerfile -t check-job .
docker tag check-job asia-northeast1-docker.pkg.dev/<project_id>/check-job-dev/check-job:latest
docker push asia-northeast1-docker.pkg.dev/<project_id>/check-job-dev/check-job:latest

# 3) 残りを構築
terraform/scripts/tf-wsl.sh apply
```

構築後、DBマイグレーションを適用する（[`../migrations/README.md`](../migrations/README.md)）。
デバイスシミュレーターはテーブルが無い間は接続・INSERTに失敗して再試行し続けるため、
マイグレーション適用後は自動的に送信を開始する。

## 2. 構築後の確認

設計書10章の手順4〜6は [`../docs/verification-runbook.md`](../docs/verification-runbook.md) に沿って行う。
特に、ログが Loki に届いた時点で **ストリームセレクタと `| json` の前提を実機で確認** し、
必要なら `grafana_loki_stream_selector` を変更して再 apply する
（[`../docs/grafana-implementation-notes.md`](../docs/grafana-implementation-notes.md)）。

## 3. 変更の反映

| 変更対象 | 反映方法 |
|---|---|
| `check_job/` のコード | イメージを再 build / push（タグ `latest`）。次回の Job 実行から反映 |
| `device_simulator/` のコード・`sim_*` 変数 | `terraform apply` でメタデータ更新後、GCE上で `sudo google_metadata_script_runner startup`（またはインスタンスをリセット） |
| `otel-collector/otel-collector-config.yaml` | 同上 |
| 閾値（`*_threshold_*` 等） | `terraform apply`（Cloud Run Job の環境変数が更新される） |
| Grafana Dashboard / Alert rule | `terraform apply` |

## 検証環境における簡略化ポイント（本番移行時に見直すこと）

- Cloud SQLの `deletion_protection` は `false`（`terraform destroy` ですぐ削除される）
- Cloud SQLは単一ゾーン・スタンバイなし、GCEインスタンスは単一台構成
- Terraform state はローカル（本番では GCS 等のリモートバックエンドへ）
- IAMは必要最小限を意識しているが、本番投入前に権限の棚卸しを推奨
