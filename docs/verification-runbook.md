# 動作確認runbook

システム全体（シミュレーター → DB → check_job → Cloud Logging → Otel Collector → Grafana Cloud）が
動作し、4種類のチェック＋チェック失敗・途絶のアラートが発火することを確認する手順（設計書10章の手順4〜6）。
以下、`environment = "dev"`、リージョン `asia-northeast1` の例。

## 0. 前提

- [`../terraform/README.md`](../terraform/README.md) の構築手順（旧環境の削除 → `terraform apply`）が完了している
- マイグレーション適用済み（[`../migrations/README.md`](../migrations/README.md)）
- `enable_device_simulator = true`

## 1. シミュレーターがデータを投入していることを確認する

```bash
gcloud compute ssh otel-collector-dev --zone=asia-northeast1-a --tunnel-through-iap
sudo systemctl status device-simulator
sudo journalctl -u device-simulator -n 20 --no-pager   # "connected; ..." が出て、エラーが続いていないこと
```

DB を直接見る場合（IAPトンネル経由で psql）：

```sql
SELECT user_id, count(*), max(registered_at), min(battery_voltage), max(battery_voltage)
FROM input_records WHERE registered_at > now() - interval '5 minutes'
GROUP BY user_id ORDER BY user_id;
```

`normal` / `delay` は約30件、`lost` は停止中なら少なく、`missing` / `never` は0件。

## 2. check_job のログを確認する

5分境界で自動実行される。手動実行する場合：

```bash
gcloud run jobs execute check-job-dev --region=asia-northeast1 --wait
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="check-job-dev" AND jsonPayload.event_type="check_result"' \
  --freshness=10m --limit=8 \
  --format="table(timestamp, jsonPayload.check_type, jsonPayload.success, jsonPayload.anomaly_user_count, jsonPayload.error_code)"
```

4種類すべての check_result が `success=True` で出ていれば正常。ログ形式は [`log-spec.md`](./log-spec.md)。

## 3. Loki に届いていることを確認し、クエリを確定する（設計書10章 手順4）

1. Otel Collector の自己メトリクスを確認（[`../otel-collector/README.md`](../otel-collector/README.md)）。
2. Grafana Cloud の Explore で `{service_name="check-job-dev"} |= "check_result" | json` を実行し、
   `event_type` / `check_type` 等がラベルとして取れることを確認する。
3. ラベル・本文の形が想定と違えば [`grafana-implementation-notes.md`](./grafana-implementation-notes.md) 5章に
   沿って `grafana_loki_stream_selector`（または `grafana.tf` のクエリ）を修正し、`terraform apply`。
4. 確定したら design.md 11章の「Lokiでのラベル／フィールドのマッピング確定」を更新する。

## 4. Dashboard を確認する（手順5）

「デバイスデータ監視 (dev)」フォルダの Dashboard を開き、4行（データ欠損・データ遅延・一時ロスト・
バッテリー低下）と稼働状況行にデータが出ていること。デフォルトのシミュレーター構成（3グループ×7台）では
おおむね次の値になる。

| 行 | 該当ユーザー数（全体） | 備考 |
|---|---|---|
| データ欠損 | 6（各グループ `missing` ＋ `never`） | 起動直後15分は `missing` 役割が lost 側に出る |
| データ遅延 | 0〜3（各グループ `delay`） | 20%の確率で遅延するため、5分間に1件以上はほぼ毎回 |
| 一時ロスト | 0〜3 | `lost` 役割は15分に1回（停止を含む時間帯）だけ該当 |
| バッテリー低下 | 変動 | 常に一部のデバイスが5V未満 |
| 監視ユーザー数 | 21 | 全行共通 |

データ遅延・バッテリー低下は該当ありのグループだけ summary が出るため（log-spec.md 6章）、
グループ別の表・推移には該当ありのグループだけが表示され、全グループ正常なら「該当なし」と出る。

## 5. アラートの発火を確認する（手順6）

「Alerts & IRM」→「Alert rules」→ フォルダ「デバイスデータ監視 (dev)」。

| ルール | 確認方法 | 期待 |
|---|---|---|
| データ欠損 | シミュレーター既定のまま | 全グループで Firing（`missing` / `never`） |
| データ遅延 | 同上 | `delay` 役割のいるグループで Firing |
| 一時ロスト | 同上（15分に1回） | 停止を含む時間帯の直後に Firing、次のチェックで Normal に戻る |
| バッテリー低下 | 同上 | 5V未満のデバイスがいるグループで Firing |
| チェック失敗 | 下記 5.1 | `DB_CONNECTION_FAILED` 等で Firing |
| チェック途絶 | 下記 5.2 | 15分＋評価間隔後に Firing |

`alert_notification_email` を設定している場合は、メール通知が届くことも確認する（未設定の場合、
contact point は作られず、アラートは Grafana の通知ポリシー（既定）に従う。発火は Alert rules 画面で確認する）。

### 5.1 チェック失敗を起こす

Cloud SQL を一時停止して接続を失敗させる（シミュレーターは再接続を繰り返すだけで影響なし）。

```bash
gcloud sql instances patch pg-dev --activation-policy=NEVER
gcloud run jobs execute check-job-dev --region=asia-northeast1 --wait   # 失敗で終了（終了コード1）
# 確認後に戻す
gcloud sql instances patch pg-dev --activation-policy=ALWAYS
```

4種類すべてで `success=false` / `error_code=DB_CONNECTION_FAILED` の check_result が出る
（lost / low_battery は `failed to determine missing users for exclusion: ...`）。
DB を止めたくない場合は、Cloud Run Job の環境変数 `DELAY_THRESHOLD_SECONDS` を一時的に `abc` にして実行すると
`CONFIG_INVALID` を確認できる（`gcloud run jobs execute check-job-dev --update-env-vars=DELAY_THRESHOLD_SECONDS=abc`）。

### 5.2 チェック途絶を起こす

```bash
gcloud scheduler jobs pause check-trigger-dev --location=asia-northeast1
# 15分以上待ち、「チェック途絶」が Firing になることを確認
gcloud scheduler jobs resume check-trigger-dev --location=asia-northeast1
```

### 5.3 実施結果（2026-11-07、dev）

アラートの状態遷移は Grafana のアラート状態履歴（データソース `grafanacloud-<stack>-alert-state-history`、
`{from="state-history"}`）で確認した。

| ルール | 操作 | 結果 |
|---|---|---|
| データ欠損・データ遅延・一時ロスト・バッテリー低下 | シミュレーター既定のまま | 3グループすべてで Firing（グループ単位のインスタンス） |
| チェック失敗 | 21:02 に `DELAY_THRESHOLD_SECONDS=abc` で実行 | 21:06 に check_type ごと（`CONFIG_INVALID`）に Firing、21:11 に Normal |
| チェック途絶 | 21:02 に Scheduler 停止 → 21:24 に再開 | 21:21 に Firing（最後の check_result から約18分）、21:26 に Normal |

補足：マイグレーション適用前の定期実行（`DB_QUERY_FAILED`）でもチェック失敗が発火した。
また、ストリームセレクタを切り替えた直後は、新しいラベルのログが15分分たまるまでチェック途絶が一時的に発火した。

## 6. 後片付け

```bash
# シミュレーターを止める（GCE上）
sudo systemctl stop device-simulator
```

デモデータの削除は [`../device_simulator/README.md`](../device_simulator/README.md)、環境ごとの削除は
`terraform destroy`。

## トラブルシューティング早見表

| 症状 | 主な原因・対処 |
|---|---|
| シミュレーターが `relation "users" does not exist` を繰り返す | マイグレーション未適用。適用すれば自動で送信開始 |
| シミュレーターが Secret Manager の 403 で起動失敗 | `enable_device_simulator = true` で apply し直す（SA への secretAccessor 付与） |
| Otel Collector が config エラー | `google_cloud_logentry_encoding` / `otlp_http` の綴り（非推奨エイリアスと混同しやすい） |
| exporter で `401 Unauthorized` | OTLP 用トークンの失効。Secret Manager 更新後、GCE で起動スクリプトを再実行（またはリセット） |
| Dashboard の Stat / Table が「No data」 | Instant クエリになっていないか。ストリームセレクタ・`| json` の前提（notes 5章） |
| Pub/Sub にメッセージが無いように見える | Collector が同じ subscription を消費している。Collector を止めてから `gcloud pubsub subscriptions pull` |
