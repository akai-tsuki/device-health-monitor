# デバイスデータ監視システム（device-health-monitor）

デバイスから定期的に登録されるデータを5分ごとにチェックし、4種類の異常
（**データ欠損・データ遅延・一時ロスト・バッテリー低下**）を Cloud Logging に出力して、
Grafana Cloud（Loki）でダッシュボード表示・アラート通知する検証用リポジトリです。

旧リポジトリ [akai-tsuki/data-update-monitor](https://github.com/akai-tsuki/data-update-monitor)
（データ欠損監視の検証）を土台に、監視対象を拡張し、出力を Cloud Logging に一本化しています
（Cloud Monitoring 関連は削除）。

> **Note**: ドキュメント中のグループ数・ユーザー数等の規模感は設計検討用のサンプル値です。

## ディレクトリ構成

```
.
├── docs/
│   ├── design.md                       # 仕様設計
│   ├── log-spec.md                     # Cloud Logging 出力仕様（インターフェース契約）
│   ├── architecture.md                 # GCP構成図
│   ├── grafana-implementation-notes.md # Grafana / Loki 実装メモ（落とし穴）
│   └── verification-runbook.md         # 4種類＋チェック失敗・途絶の発火確認手順
├── migrations/          # 0001_init.sql + apply_migrations.py
├── check_job/           # Cloud Run Job（Python）。checkers/ に4種類のチェック、tests/ に単体テスト
├── device_simulator/    # デモ用デバイスシミュレーター（GCE上で systemd 常駐）
├── otel-collector/      # Otel Collector 設定（logs パイプラインのみ）
└── terraform/           # GCP / Grafana Cloud 構築一式（旧環境の削除手順も記載）
```

## ドキュメント

| 内容 | 参照先 |
|---|---|
| 仕様・判定ロジック | [`docs/design.md`](./docs/design.md) |
| ログ形式 | [`docs/log-spec.md`](./docs/log-spec.md) |
| 旧環境の削除・構築手順 | [`terraform/README.md`](./terraform/README.md) |
| 動作確認 | [`docs/verification-runbook.md`](./docs/verification-runbook.md) |
| チェック処理 | [`check_job/README.md`](./check_job/README.md) |
| シミュレーター | [`device_simulator/README.md`](./device_simulator/README.md) |
| マイグレーション | [`migrations/README.md`](./migrations/README.md) |
| Otel Collector | [`otel-collector/README.md`](./otel-collector/README.md) |

## テスト

```bash
python -m venv .venv
.venv/bin/pip install -r check_job/requirements-dev.txt     # Windows: .venv\Scripts\pip
.venv/bin/python -m pytest
```

## 認証情報の取り扱いについて

以下はリポジトリにコミットしないでください（`.gitignore` で除外済み）。

- `terraform/terraform.tfvars`（`terraform.tfvars.example` はコミット可）
- `*.tfstate` / `*.tfstate.*`（Cloud SQL のパスワード等を含む）
- 認証情報の実値
  - DBパスワード（Terraform がランダム生成し Secret Manager へ格納）
  - `grafana_cloud_api_key` — Grafana Cloud **OTLP送信用** のトークン（Otel Collector が使用）
  - `grafana_sa_token` — Grafana スタック内の **Dashboard / Alert rule 管理用** Service Account Token
    （Terraform の `grafana` プロバイダが使用。上記とは別物）

## 現状のステータス（設計書10章）

- [ ] 1. 旧環境を `terraform destroy`（手順は [`terraform/README.md`](./terraform/README.md) 0章）
      — 2026-11-07 に Grafana 側を含めほぼ削除済み。PSA 接続・予約範囲 `psa-cloudsql-validation`・
      VPC `vpc-validation` の3件は、Cloud SQL 削除後のピアリング解放待ちで残っている
      （料金なし・新環境と名前の衝突なし）。2026-11-08 の再試行でも解放されていなかったため、数日おいて再実行する。
      旧設定の Grafana データソース参照は失効トークンで 401 になるため、再実行時は
      `-target=google_service_networking_connection.private_vpc_connection -target=google_compute_global_address.private_ip_alloc -target=google_compute_network.vpc -target=google_project_service.apis`
      を付けて Google Cloud 側だけを削除する
- [x] 2. 旧リポジトリから構成を移植し、Cloud Monitoring 関連を削除
- [x] 3. マイグレーション・`check_job`（4チェック＋テスト）・`device_simulator` を実装
      （ローカルの PostgreSQL 15 で E2E 確認済み）
- [x] 4. Terraform で Google Cloud を構築、ログが Loki に届くことを確認し、8.1 のクエリを確定
      （2026-11-07、`environment = "dev"`。ストリームは `{service_name="check-job-dev"}`）
- [x] 5. Grafana Dashboard / Alert rule を作成（`terraform/grafana.tf`。Dashboard の各クエリの値は API で確認済み、
      画面での見た目の確認は未）
- [x] 6. `verification-runbook.md` に沿って発火を確認（2026-11-07。4種類はグループ単位で発火、
      チェック失敗は `CONFIG_INVALID` で発火→5分後に解消、チェック途絶は Scheduler 停止約18分後に発火→再開後に解消）
