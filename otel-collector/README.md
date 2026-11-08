# Otel Collector設定

GCEインスタンス（`otel-collector-<environment>`）上で稼働する Otel Collector の設定。
旧リポジトリから **logs パイプラインのみ** を残し、`googlecloudmonitoring` receiver とメトリクス
パイプラインを削除した（チェック結果は Cloud Logging のみに出力する。[`../docs/log-spec.md`](../docs/log-spec.md)）。

## 構成

- `otel-collector-config.yaml` — Collector 設定テンプレート。`${...}` は `terraform/gce_otel_collector.tf` の
  `templatefile()` が apply 時に埋め込む。`__GRAFANA_CLOUD_API_KEY__` だけは GCE 起動スクリプト
  （[`../terraform/scripts/otel_collector_startup.sh`](../terraform/scripts/otel_collector_startup.sh)）が
  Secret Manager から取得して置換する（APIキーをメタデータに平文で残さないため）。

## 動作の流れ

1. Cloud Run Job（`check_job`）の標準出力 JSON が Cloud Logging に取り込まれる。
2. Log Router sink（`terraform/pubsub_logging.tf`）が Cloud Run Job のログを Pub/Sub トピックへ転送する。
3. `googlecloudpubsub` receiver がサブスクリプションを購読し、`google_cloud_logentry_encoding` extension
   （`handle_json_payload_as: json`）で LogEntry をデコードする。
4. `otlp_http` exporter が Grafana Cloud（Loki）へ送信する。

Cloud Logging を直接ポーリングする receiver は opentelemetry-collector-contrib に存在しない
（[Issue #34056](https://github.com/open-telemetry/opentelemetry-collector-contrib/issues/34056) は
"not planned"）ため、この Log Router → Pub/Sub 経由の構成を取っている。

## 旧リポジトリで判明済みの注意点

- extension ID は `google_cloud_logentry_encoding`（`googlecloudlogentry_encoding` は非推奨エイリアス）
- exporter ID は `otlp_http`（`otlphttp` は非推奨エイリアス）
- `googlecloudpubsub` receiver の組み込み encoding `cloud_logging` は v0.132.0 で廃止済み
- `otelcol-contrib` サービスは専用ユーザーで動くため、`config.yaml` の所有者・権限を合わせる必要がある

## 動作確認方法

```bash
gcloud compute ssh otel-collector-dev --zone=asia-northeast1-a --tunnel-through-iap

sudo systemctl status otelcol-contrib
sudo journalctl -u otelcol-contrib -n 50 --no-pager

# 自己メトリクス（デフォルトで公開）で受信・送信件数を切り分ける
curl -s http://127.0.0.1:8888/metrics | grep -E 'receiver_(accepted|refused)|exporter_(sent|send_failed)'
```

`accepted` は増えるのに `exporter_sent` が増えない場合は exporter 側（認証・エンドポイント）を、
`accepted` が0のままなら receiver 側（Pub/Sub 購読・デコード）を疑う。
