# Cloud Logging 出力仕様（ログ仕様 v1.1）

チェックアプリ（`check_job`）が Cloud Logging へ書き込むログの**インターフェース契約**。
Otel Collector / Grafana Cloud 側の設定変更や、チェックアプリを別実装で作り直す場合は、
このドキュメントを一次情報として参照すること。判定ロジックの背景は [`design.md`](./design.md) を参照。

> 旧リポジトリ（data-update-monitor）では Cloud Monitoring のカスタムメトリクスも出力していたが、
> 本リポジトリでは**ログに一本化**する。Grafana Cloud の数値パネル・アラートはすべてサマリログから作る。

## 1. 出力経路

- Cloud Run Job の**標準出力**に、1行1件のJSONを書き出す（Cloud Run が自動で Cloud Logging に取り込む）。
- `severity` と `message` は Cloud Logging が特別扱いし、LogEntry のログレベル・表示文になる。
  それ以外のフィールドは `jsonPayload` 配下に入る。
- 後段：Cloud Logging → Log Router sink → Pub/Sub → Otel Collector → Grafana Cloud（Loki）。

## 2. ログの種類

| `event_type` | 出力単位 | 用途 |
|---|---|---|
| `check_result` | 1回の実行 × チェック種類ごとに1件 | チェックアプリの稼働確認・失敗検知 |
| `summary` | 1回の実行 × チェック種類 × グループ × ユーザタイプごとに1件（`missing` / `lost` は**該当0件でも出す**。`delay` / `low_battery` は該当1件以上のときだけ出す。6章） | アラート・ダッシュボードの数値 |
| `detail` | 1回の実行 × チェック種類 × 該当ユーザーごとに1件 | 該当ユーザーIDと詳細の確認 |

| `check_type` | 内容 | 異常時の `status` |
|---|---|---|
| `missing` | データ欠損 | `missing` |
| `delay` | データ遅延 | `delayed` |
| `lost` | 一時ロスト | `lost` |
| `low_battery` | バッテリー低下 | `low_battery` |

## 3. 出力順序

1回の実行で、チェック種類ごとに **detail → summary → check_result** の順に出力する。
**check_result は必ずそのチェック種類の最後**に出す。

- 後段は「`success=true` の check_result がある `run_id` のサマリのみ信頼できる」と判断できる。
- チェックが途中で失敗した場合も、例外を捕捉して `success=false` の check_result を出力する
  （プロセスごと落ちて出力できない場合は、check_result の途絶としてアラートで検知する）。

## 4. 共通フィールド

全ログに含める。

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `severity` | string | ○ | Cloud Logging のログレベル。値は5章以降で種類ごとに規定 |
| `message` | string | ○ | 表示用の固定文言。値は5章以降で規定 |
| `schema_version` | string | ○ | ログ仕様のバージョン。本仕様は `"1.1"` |
| `event_type` | string | ○ | `check_result` / `summary` / `detail` |
| `check_type` | string | ○ | `missing` / `delay` / `lost` / `low_battery` |
| `record_id` | string | ○ | ログ1件ごとの一意ID（UUID v4） |
| `run_id` | string | ○ | 1回の実行で共通のID。Cloud Run の実行名（環境変数 `CLOUD_RUN_EXECUTION`）を使う。ローカル実行時はUUID |
| `checked_at` | string | ○ | チェック基準時刻（ISO 8601、タイムゾーン付き）。1回の実行で全ログ共通。6.1節参照 |
| `env` | string | ○ | 環境名（`dev` / `stg` / `prod` 等）。環境変数 `ENV` |

### 4.1 型・表記の共通ルール

- 日時はすべて ISO 8601、タイムゾーンオフセット付き（例 `2026-11-07T17:20:00+09:00`）。
  タイムゾーンは環境変数 `TZ_NAME`（デフォルト `Asia/Tokyo`）。
- 秒数・件数は整数。電圧は小数第2位までの数値（例 `4.85`）。
- 値がない場合はフィールドを省略せず `null` を入れる。
- `status` などのコード値はすべて小文字英字・スネークケース。

## 5. チェック結果（`event_type = check_result`）

| フィールド | 型 | 説明 |
|---|---|---|
| `success` | boolean | チェック成功なら `true`、失敗なら `false` |
| `duration_ms` | integer | そのチェック種類の処理にかかった時間（ミリ秒） |
| `group_count` | integer \| null | 対象グループ数。失敗時は `null` |
| `monitored_user_count` | integer \| null | 全グループの監視ユーザ数。失敗時は `null` |
| `anomaly_user_count` | integer \| null | 全グループの該当ユーザ数（`check_type` に応じた異常のユーザ数）。失敗時は `null` |
| `error_code` | string \| null | 失敗の分類コード（下表）。成功時は `null` |
| `error_message` | string \| null | 調査用の情報（例外メッセージ等、チェックアプリが任意に設定）。成功時は `null` |

| `success` | `severity` | `message` |
|---|---|---|
| `true` | `INFO` | `check completed` |
| `false` | `ERROR` | `check failed` |

### 5.1 エラーコード

| `error_code` | 意味 |
|---|---|
| `CONFIG_INVALID` | 環境変数・設定値の不備 |
| `DB_CONNECTION_FAILED` | DB接続失敗 |
| `DB_QUERY_FAILED` | クエリ実行失敗（タイムアウト含む） |
| `UNKNOWN` | 上記以外の想定外エラー |

コードの追加は可（追加時はこの表を更新し、`schema_version` のマイナーバージョンを上げる）。

## 6. サマリ（`event_type = summary`）

| フィールド | 型 | 説明 |
|---|---|---|
| `group_id` | string | グループID |
| `user_type` | string | ユーザタイプ（`users.user_type`）。現時点では1種類のみ |
| `monitored_user_count` | integer | 対象グループ×ユーザタイプの監視ユーザ数 |
| `anomaly_user_count` | integer | 対象グループ×ユーザタイプの該当ユーザ数（0以上） |
| `status` | string | `anomaly_user_count = 0` なら `normal`、1以上なら2章の異常時 `status` |

| `status` | `severity` | `message` |
|---|---|---|
| `normal` | `INFO` | `group is normal` |
| 異常（`missing` 等） | `WARNING` | `group has anomaly users` |

出力ルール：

- 対象は、`users.status = 'active'` のユーザーが1人以上いるグループ×ユーザタイプ。
- `missing` / `lost`：該当ユーザーが0人でも `anomaly_user_count = 0`, `status = normal` で**必ず出力**する
  （出力がない＝健全、ではなく、出力がない＝チェックされていない、と区別するため）。
- `delay` / `low_battery`：該当ユーザーが1人以上のグループ×ユーザタイプだけ出力する
  （`status = normal` のサマリは出さない）。グループ数が増えても正常時のログ件数が増えないようにするため。
  チェックが実行されて正常だったことは、同じ `run_id` の `check_result`（`success = true`）で判断する。
- アクティブユーザーが1人もいないグループは出力しない。

### 6.1 監視ユーザ数の定義

`monitored_user_count` は全チェック種類で共通に「`users.status = 'active'` のユーザー数」とする。
チェック種類によって判定対象から除外されるユーザー（下記）も母数には含める。

- `lost` / `low_battery`：同じ実行で `missing` と判定されたユーザーは判定対象外（該当にしない）
- `lost`：一度もデータ登録がないユーザーも判定対象外（`missing` 側で `last_registered_at = null` として出る）

## 7. 詳細（`event_type = detail`）

### 7.1 全チェック種類共通

| フィールド | 型 | 説明 |
|---|---|---|
| `group_id` | string | グループID |
| `user_id` | string | ユーザID |
| `user_type` | string | ユーザタイプ |
| `status` | string | 2章の異常時 `status`（`missing` / `delayed` / `lost` / `low_battery`） |

`severity` は `WARNING`、`message` は下表。

| `check_type` | `message` |
|---|---|
| `missing` | `user data missing` |
| `delay` | `user data delayed` |
| `lost` | `user data temporarily lost` |
| `low_battery` | `user battery low` |

### 7.2 `check_type = missing`（データ欠損）

| フィールド | 型 | 説明 |
|---|---|---|
| `last_registered_at` | string \| null | 最終データ登録日時（`registered_at` の最大値）。一度も登録がなければ `null` |
| `threshold_seconds` | integer | 判定閾値（デフォルト `900` = 15分） |
| `elapsed_seconds` | integer \| null | `checked_at - last_registered_at`（秒）。一度も登録がなければ `null` |

### 7.3 `check_type = delay`（データ遅延）

ユーザー単位で1件出力する（遅延レコードが複数あっても1件にまとめる）。

| フィールド | 型 | 説明 |
|---|---|---|
| `window_start` | string | 判定対象時間帯の開始（含む） |
| `window_end` | string | 判定対象時間帯の終了（含まない）。= `checked_at` |
| `threshold_seconds` | integer | 判定閾値（デフォルト `20`） |
| `delayed_record_count` | integer | 時間帯内で遅延と判定したレコード数 |
| `max_delay_seconds` | integer | 時間帯内の最大遅延秒数（`registered_at - processed_at`、切り捨て） |
| `max_delay_processed_at` | string | 最大遅延レコードの `processed_at` |
| `max_delay_registered_at` | string | 最大遅延レコードの `registered_at` |

### 7.4 `check_type = lost`（一時ロスト）

| フィールド | 型 | 説明 |
|---|---|---|
| `window_start` | string | 判定対象時間帯の開始（含む） |
| `window_end` | string | 判定対象時間帯の終了（含まない）。= `checked_at` |
| `record_count` | integer | 時間帯内に登録されたレコード数（0以上） |
| `threshold_count` | integer | 判定閾値（デフォルト `8`。この件数**以下**で該当） |

### 7.5 `check_type = low_battery`（バッテリー低下）

| フィールド | 型 | 説明 |
|---|---|---|
| `battery_voltage` | number | 最新レコードの電圧（V） |
| `threshold_voltage` | number | 判定閾値（デフォルト `5.00`。この値**未満**で該当） |
| `last_registered_at` | string | 最新レコードの `registered_at` |

## 8. 出力例

```json
{"severity":"WARNING","message":"user data delayed","schema_version":"1.1","event_type":"detail","check_type":"delay","record_id":"7d2b0c1e-...","run_id":"check-job-dev-x7k2p","checked_at":"2026-11-07T17:20:00+09:00","env":"dev","group_id":"demo-group-1","user_id":"demo-user-g1-delay-01","user_type":"default","status":"delayed","window_start":"2026-11-07T17:15:00+09:00","window_end":"2026-11-07T17:20:00+09:00","threshold_seconds":20,"delayed_record_count":3,"max_delay_seconds":41,"max_delay_processed_at":"2026-11-07T17:17:02+09:00","max_delay_registered_at":"2026-11-07T17:17:43+09:00"}
{"severity":"WARNING","message":"group has anomaly users","schema_version":"1.1","event_type":"summary","check_type":"delay","record_id":"c41f9a0d-...","run_id":"check-job-dev-x7k2p","checked_at":"2026-11-07T17:20:00+09:00","env":"dev","group_id":"demo-group-1","user_type":"default","monitored_user_count":5,"anomaly_user_count":1,"status":"delayed"}
{"severity":"INFO","message":"check completed","schema_version":"1.1","event_type":"check_result","check_type":"delay","record_id":"0a9e5f72-...","run_id":"check-job-dev-x7k2p","checked_at":"2026-11-07T17:20:00+09:00","env":"dev","success":true,"duration_ms":412,"group_count":3,"monitored_user_count":12,"anomaly_user_count":2,"error_code":null,"error_message":null}
{"severity":"ERROR","message":"check failed","schema_version":"1.1","event_type":"check_result","check_type":"missing","record_id":"e5b3d811-...","run_id":"check-job-dev-q2m9t","checked_at":"2026-11-07T17:25:00+09:00","env":"dev","success":false,"duration_ms":30012,"group_count":null,"monitored_user_count":null,"anomaly_user_count":null,"error_code":"DB_CONNECTION_FAILED","error_message":"connection to server at 10.10.0.3 timed out"}
```

## 9. 互換性ルール

- フィールドの**追加**は後方互換とみなし、`schema_version` のマイナーを上げる（`1.0` → `1.1`）。
- フィールドの削除・改名・型変更・コード値の意味変更は非互換とし、メジャーを上げる（`2.0`）。
  Grafana のクエリ・アラートも合わせて更新する。

## 10. 変更履歴

| バージョン | 日付 | 内容 |
|---|---|---|
| `1.0` | 2026-11-07 | 初版 |
| `1.1` | 2026-11-08 | `delay` / `low_battery` の summary を該当1件以上のグループ×ユーザタイプだけに変更（6章）。フィールドとコード値の意味は変更なし |
