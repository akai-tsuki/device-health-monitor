# Grafana / Loki 実装メモ — 落とし穴と対処

正式な仕様は [`design.md`](./design.md) 8章・[`log-spec.md`](./log-spec.md)、実際のパネル・ルールは
[`../terraform/grafana.tf`](../terraform/grafana.tf)。このメモは実装時に判明した（または判明させるべき）
Grafana 固有の注意点の記録。1〜4章は旧リポジトリ（data-update-monitor、2026-07）から引き継いだもの。

## 旧リポジトリから引き継いだ落とし穴

### 1. Stat / Table パネルで Instant クエリが機能しなかった

Alert rule では `instant` が機能する一方、Dashboard の Stat / Table では「No data」になった
（原因未特定）。**対処**：パネルは Range クエリにし、Stat は `lastNotNull`、Table は `reduce`
transformation で最新値を表示する。新リポジトリの `grafana.tf` もこの方式。

### 2. `reduce` transformation の `mode` は `seriesToRows`

複数系列（グループごと）を「1系列＝1行」にするには `seriesToRows`。`reduceFields` だと横に並んだ
1行のワイドテーブルになり、先頭の系列しか見えない。

### 3. Terraform でリソースアドレスを変えても `name` を変えないと衝突する

`grafana_contact_point` 等の `name` が同じだと Grafana 側では同一オブジェクトとなり、旧リソース削除が
409 Conflict になる。新リポジトリでは contact point 名に環境名を含めた
（`device-health-monitor-email-<environment>`）。既に衝突した場合は `terraform state rm` で旧アドレスを外す。

### 4. `terraform fmt` は引数なしだと `terraform.tfvars` も対象にする

diff として実値（認証情報）が表示されるため、`terraform fmt -check -diff main.tf variables.tf` のように
対象ファイルを明示する。

## 新リポジトリ（Loki 一本化）での注意点

### 5. Loki 上のラベル・本文（design.md 8.1節、2026-11-07 実機で確定）

Otel Collector v0.165.0 → Grafana Cloud（OTLP）で確認した結果：

| 項目 | 実際の形 |
|---|---|
| インデックスラベル | `service_name` のみ。Otel Collector で何もしないと **`unknown_service`** になる |
| ログ本文 | jsonPayload の JSON 文字列（キーはアルファベット順。`severity` は含まれず、`message` は含まれる） |
| structured metadata | `gcp_label_job_name`、`gcp_label_run_googleapis_com_execution_name`、`gcp_resource_type`、`severity_text`、`log_record_uid`（1行ごとに一意）など |

これを踏まえて次のように確定した。

1. **ストリームセレクタ**：Otel Collector の `resource/service_name` processor で `service.name` を
   `check-job-<environment>` に上書きし、`{service_name="check-job-dev"}` で選択する
   （`var.grafana_loki_stream_selector` が空のときの既定値）。`unknown_service` のままでも
   `{service_name="unknown_service"} | gcp_label_job_name="check-job-dev"` で絞れるが、インデックスで絞れる方が軽い。
2. **`| json`**：本文が JSON なのでそのまま使える。`|= "summary"` 等の行フィルタも有効。
3. **数値クエリの系列分割**：structured metadata の `log_record_uid` が1行ごとに一意なため、
   `max_over_time(... | unwrap ...)` の内側では1行＝1系列になる。必ず外側で `max by (...)` / `sum by (...)` に
   集約すること（6章）。

確認用クエリ（Explore、Code モード）：

```logql
{service_name="check-job-dev"} |= "check_result" | json
```

### 6. `| json` を引数なしで数値クエリに使わない

引数なしの `| json` は `record_id`・`run_id` 等の一意な値までラベルにするため、ログ1行ごとに別系列になる。
数値クエリでは必要なフィールドだけを抽出し（`| json event_type="event_type", ...`）、外側で
`sum by (group_id)` / `max by (...)` に集約する。ログパネル（詳細ログ）では引数なしで問題ない。

### 7. 時間範囲 `[6m]` と「最新値」

チェックは5分間隔のため、`[6m]` で直近1回分（境界の揺らぎで2回分のこともある）を拾い、`max` で集約する。
回復直後は最大1回分（5分）古い値が残りうる。全グループ合計（Stat）は check_result の
`anomaly_user_count` / `monitored_user_count`（`success="true"` のみ）から取り、グループ別は summary から取る。

### 8. チェック途絶アラートは No Data で発火させる

直近15分に check_result が0件だと `count_over_time` の結果は空（No Data）になり、0 という値にはならない。
そのため「チェック途絶」ルールだけ `no_data_state = "Alerting"` にしている（他のルールは `OK`）。
Loki のクエリエラー時は `exec_err_state = "Error"`。
