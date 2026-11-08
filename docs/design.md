# デバイスデータ監視システム 仕様設計

旧リポジトリ [akai-tsuki/data-update-monitor](https://github.com/akai-tsuki/data-update-monitor)
（データ欠損監視の検証）を土台に、監視対象を4種類（データ欠損・データ遅延・一時ロスト・
バッテリー低下）へ拡張し、出力を Cloud Logging に一本化した新リポジトリの仕様。

ログの厳密な形式は [`log-spec.md`](./log-spec.md) を参照。

> **Note**：グループ数・ユーザー数等の規模感は設計検討用のサンプル値であり、実際の事業・
> システムの規模を示すものではない。

## 1. 旧リポジトリからの主な変更点

| 項目 | 旧（data-update-monitor） | 新 |
|---|---|---|
| 監視種類 | データ欠損のみ | データ欠損・データ遅延・一時ロスト・バッテリー低下 |
| チェック結果の出力 | Cloud Monitoring（メトリクス）＋ Cloud Logging（詳細ログ） | **Cloud Loggingのみ**（チェック結果・サマリ・詳細の3種類） |
| Otel Collector | `googlecloudmonitoring` + `googlecloudpubsub` receiver | `googlecloudpubsub` receiverのみ |
| Grafana のデータソース | Mimir（数値）＋ Loki（ログ） | **Lokiのみ**（数値もサマリログから集計） |
| サンプルデータ | 1分ごとにCloud Run Jobで投入 | **デバイスシミュレーター**が10秒ごとに常時投入 |
| DB | `users`, `input_records(processed_at, registered_at)` | `users.user_type`, `input_records.battery_voltage` を追加 |

旧構成から引き継ぐもの：VPC / Cloud SQL（プライベートIP）/ Secret Manager / Artifact Registry /
Cloud Run Job + Cloud Scheduler / Log Router sink + Pub/Sub / GCE上のOtel Collector /
Terraform（`grafana` プロバイダ含む）/ `checker`（純粋ロジック）と `repository`（DB I/O）の分離 /
マイグレーション方式。

旧環境の Google Cloud リソースはデータ保全不要のため、`terraform destroy`（Grafana側リソース含む）
で削除した上で、新リポジトリのTerraformで構築し直す。

## 2. 前提・パラメータ

| 項目 | 値 | 設定 |
|---|---|---|
| デバイスの送信間隔（想定） | 10秒に1回（5分で約30件） | シミュレーター `SIM_SEND_INTERVAL_SECONDS` |
| 通常の登録遅延 | 1〜2秒 | シミュレーター |
| チェック間隔 | 5分 | Terraform `check_interval_minutes` |
| データ欠損閾値 | 15分（900秒）超 | `MISSING_THRESHOLD_SECONDS=900` |
| データ遅延閾値 | 20秒以上 | `DELAY_THRESHOLD_SECONDS=20` |
| 一時ロスト：集計時間帯 | 5分 | `LOST_WINDOW_MINUTES=5`（チェック間隔と同じ値にすること） |
| 一時ロスト：閾値 | 8件以下 | `LOST_MAX_RECORDS=8` |
| バッテリー低下閾値 | 5V未満（5.00Vは正常） | `LOW_BATTERY_THRESHOLD_VOLTAGE=5.00` |
| ユーザタイプ | 現時点では1種類（`default`） | `users.user_type` |

## 3. データフロー

```
デバイスシミュレーター（GCE上で常駐、10秒ごと）
   ↓ INSERT
Cloud SQL (PostgreSQL)  users / input_records
   ↑ 5分ごとにクエリ
Cloud Scheduler → Cloud Run Job（check_job：4種類のチェック）
   ↓ 標準出力に JSON（check_result / summary / detail）
Cloud Logging → Log Router sink → Pub/Sub
   ↓ googlecloudpubsub receiver（google_cloud_logentry_encoding）
GCE上の Otel Collector → otlphttp → Grafana Cloud（Loki）
   ├─ Dashboard：チェック種類ごとの該当ユーザー数・推移・詳細ログ・チェック稼働状況
   └─ Alert rule：チェック種類ごと（該当 > 0）＋ チェック失敗 ＋ チェック途絶
```

実際のシステムでは「デバイス → 受信側 → DB登録」の経路があるが、監視対象はDBに登録された
データのため、検証ではシミュレーターがDBへ直接INSERTする。

## 4. データベース設計

```sql
CREATE TABLE users (
    user_id    TEXT PRIMARY KEY,
    group_id   TEXT NOT NULL,
    user_type  TEXT NOT NULL DEFAULT 'default',  -- ユーザをグルーピングするタイプのコード
    status     TEXT NOT NULL DEFAULT 'active'    -- 'active' or 'disabled'
);
CREATE INDEX idx_users_group  ON users (group_id);
CREATE INDEX idx_users_status ON users (status);

CREATE TABLE input_records (
    id               BIGSERIAL PRIMARY KEY,
    user_id          TEXT NOT NULL,
    processed_at     TIMESTAMPTZ NOT NULL,   -- デバイス側でデータを処理・送信した時刻
    registered_at    TIMESTAMPTZ NOT NULL,   -- DBに登録された時刻
    battery_voltage  NUMERIC(4,2)            -- デバイスのバッテリー電圧（V）。NULL許容
);
CREATE INDEX idx_input_records_user_registered
    ON input_records (user_id, registered_at DESC);
CREATE INDEX idx_input_records_registered
    ON input_records (registered_at);
```

新リポジトリではマイグレーションを `0001_init.sql` 1本にまとめ直す（旧データは保全不要）。

## 5. チェックの時間基準

- `checked_at`：Job起動時刻を**5分境界に切り捨てた時刻**（例：17:20:03起動 → 17:20:00）。
  Cloud Schedulerは `*/5` で起動するため、起動の数秒の遅れを吸収でき、前回時刻の保存が不要になる。
- 判定対象時間帯（遅延・一時ロスト）：`[checked_at - 5分, checked_at)`、`registered_at` 基準。
  連続するチェックで時間帯が重ならず、取りこぼしもない。
- 欠損・バッテリーの「最新レコード」も `registered_at < checked_at` の範囲で判定する
  （同一実行内で基準時刻を揃えるため）。
- 1回の実行で4種類のチェックを順に行い、すべて同じ `checked_at` と `run_id` を使う。

## 6. 判定ロジック

対象はすべて `users.status = 'active'` のユーザー。

### 6.1 データ欠損（`missing`）

```
last_registered_at が NULL（一度も登録なし）
または checked_at - last_registered_at > 900秒
```

旧リポジトリと同じ。LATERAL JOIN で各ユーザーの最新 `registered_at` を取得する。

### 6.2 データ遅延（`delay`）

```
時間帯内に registered_at - processed_at >= 20秒 のレコードが1件以上あるユーザー
```

```sql
SELECT ir.user_id,
       COUNT(*)                                                    AS delayed_record_count,
       MAX(EXTRACT(EPOCH FROM ir.registered_at - ir.processed_at)) AS max_delay_seconds
FROM input_records ir
JOIN users u ON u.user_id = ir.user_id AND u.status = 'active'
WHERE ir.registered_at >= %(window_start)s
  AND ir.registered_at <  %(window_end)s
  AND ir.registered_at - ir.processed_at >= make_interval(secs => %(threshold)s)
GROUP BY ir.user_id;
```

（最大遅延レコードの `processed_at` / `registered_at` は `DISTINCT ON` 等で取得する）

- 遅延はデバイスとDBの時計が一致している前提。実運用ではデバイスの時計ずれも遅延として
  計上される点に留意（検証ではシミュレーターが両方の時刻を生成するため問題にならない）。

### 6.3 一時ロスト（`lost`）

```
時間帯内に登録されたレコード数が 8件以下 のユーザー（0件も含む）
ただし、同じ実行で missing と判定されたユーザーは除外
```

- 一度も登録がないユーザーは missing（`last_registered_at = null`）になるため、自動的に除外される。
- データが完全に止まったユーザーは、止まってから15分以内は lost として出る。15分を超えると
  missing となり、lost からは外れる。
- 遅れて届いたデータは登録された時間帯で数える（遅延は数十秒程度のため影響は軽微）。
- 閾値8件は送信間隔10秒を前提とした値。将来、送信間隔の異なるユーザタイプが増えた場合は
  ユーザタイプごとに閾値を持たせる。

### 6.4 バッテリー低下（`low_battery`）

```
最新レコード（registered_at < checked_at の最大）の battery_voltage < 5.00
ただし、同じ実行で missing と判定されたユーザーは除外（古い電圧で判定しないため）
battery_voltage が NULL のレコードは判定に使わない（電圧を持つ最新レコードで判定）
```

### 6.5 実装構造

- `repository.py`：DBアクセス。チェック種類ごとに必要なデータを取得する関数を持つ。
- `checkers/`：チェック種類ごとの純粋ロジック（DB・GCPに依存しない）。共通インターフェース
  `check(data, params, checked_at) -> CheckOutcome(details, summaries, totals)`。
- `reporter.py`：`log-spec.md` に従ってJSONを組み立て、標準出力に書く。
- `main.py`：実行順は `missing` → `delay` → `lost` → `low_battery`（lost / low_battery は
  missing の結果を除外に使う）。各チェック種類は独立に try/except し、1種類の失敗で他を止めない。
  missing が失敗した場合、lost / low_battery は除外対象を求めるため missing の判定を1回だけ再試行し、
  それも失敗したら **失敗扱い**（`success=false`、`error_message` は
  `failed to determine missing users for exclusion: ...`）とする（実装時に決定。除外なしで判定すると
  missing ユーザーが lost / low_battery に誤って計上されるため）。
- サマリの出力範囲：`missing` / `lost` は全グループ×ユーザタイプ分（該当0でも出す）、
  `delay` / `low_battery` は該当1人以上のグループ×ユーザタイプだけを出す（2026-11-08 決定。
  グループ数が増えたときに正常時のログ件数が増えないようにするため。log-spec.md 6章）。
  全体の合計（該当数・監視数）は `check_result` に出るため、どのチェック種類でも影響しない。
- 単体テスト（pytest）：各チェックの境界値（閾値ちょうど・NULL・0件・除外）を必ずテストする。

## 7. デバイスシミュレーター（`device_simulator/`）

検証・デモ専用。本番のチェック処理とは無関係。

### 7.1 動作

- 常駐プロセスとして10秒ごとに、各仮想デバイス（= ユーザー）のレコードをINSERTする。
  - `registered_at = now()`、`processed_at = now() - 遅延`（通常1〜2秒のランダム）
- 起動時に `demo-group-1`〜`N` とユーザーを作成（既存なら維持）。役割はユーザーIDに埋め込む
  （`demo-user-g{グループ}-{役割}-{連番}`）。

| 役割 | 動き | 主に検知されるチェック |
|---|---|---|
| `normal` | 10秒ごとに送信、遅延1〜2秒 | なし |
| `delay` | 一定割合（例：20%）のレコードを遅延20〜60秒で登録 | delay |
| `lost` | 例：15分周期で3〜4分間送信を停止し、その後再開 | lost |
| `missing` | 初回に1件だけ登録し、以降送信しない | missing |
| `never` | 一度も送信しない | missing（`last_registered_at = null`） |

- バッテリー電圧：全デバイスが10V→0Vへ一定速度で下がり、0Vに達したら10Vに戻る（充電の模擬）
  ノコギリ波。周期（例：30〜90分）と初期位相はデバイスごとにランダムにし、常に一部のデバイスが
  5V未満となるようにする。値は小数第2位で丸める。
- 遅延レコードは「遅れて登録された」ことを表すため、`processed_at` を遡らせて生成する
  （登録自体を遅らせる必要はない）。
- `--cleanup` で `demo-user-*` と関連レコードを全削除する。

### 7.2 稼働場所

- 既存のOtel Collector用GCE上で systemd サービス（`device-simulator.service`）として稼働させる
  （VPC内でCloud SQLのプライベートIPに直接届き、常駐プロセスに向くため）。
- Terraform変数 `enable_device_simulator` で有無を切り替える。DBパスワードは起動時に
  Secret Managerから取得する（GCEのSAに `secretmanager.secretAccessor` を追加）。
- 開始・停止：`sudo systemctl start|stop device-simulator`（IAP SSH経由）。
- データ量：10秒ごと × デバイス数。デモ規模（数グループ×数台）なら1日で数万行程度のため、
  古いレコードを定期削除する（例：シミュレーターが1時間ごとに24時間より前を削除）。

## 8. Grafana Cloud

Terraform（`grafana` プロバイダ）で管理する。すべて Loki のサマリ／詳細／チェック結果ログが元データ。

### 8.1 クエリの基本形

```logql
# チェック種類 delay のグループ別該当ユーザー数
max by (group_id) (
  max_over_time({service_name=~".+"} | json
    | event_type="summary" | check_type="delay"
    | unwrap anomaly_user_count [6m])
)
```

> ラベル（`service_name` 等）と、`jsonPayload` が Loki 上でログ本文になるか structured metadata
> になるかは Otel Collector のマッピング次第のため、**実装初期に実機で確認してクエリを確定**する
> （旧リポジトリでは `{service_name=~".+"} |= "<message>"` で詳細ログを検索できていた）。

### 8.2 Dashboard

チェック種類ごとに1行（row）、計4行＋稼働状況1行。各行：

1. 該当ユーザー数（全グループ合計、最新値）
2. 監視ユーザー数（全グループ合計、最新値）
3. グループ別該当ユーザー数（多い順テーブル）
4. 該当ユーザー数の推移（グループ別時系列）
5. 詳細ログ（`event_type="detail"`、user_id 確認用）

`delay` / `low_battery` の行では、正常なグループはサマリが出ないため、3・4 には該当ありのグループだけが
表示される（全グループ正常なら「該当なし」）。

稼働状況行：チェック種類ごとの最新 `success`、`duration_ms` の推移、失敗ログ一覧。
旧リポジトリの実装メモ（Instantクエリ、`reduce` の `seriesToRows`）を参照すること。

### 8.3 Alert rule

| ルール | 条件（概要） | 通知内容の例 |
|---|---|---|
| データ欠損 | summary / `check_type=missing` の `anomaly_user_count > 0`（グループ別） | グループAで3人データ欠損しています |
| データ遅延 | 同 `delay` | グループAで1人データ遅延が発生しています |
| 一時ロスト | 同 `lost` | グループAで2人一時ロストが発生しています |
| バッテリー低下 | 同 `low_battery` | グループAで2人バッテリーが低下しています |
| チェック失敗 | check_result の `success=false` が1件以上 | delay チェックが失敗しました（DB_CONNECTION_FAILED） |
| チェック途絶 | check_result が直近15分間に0件（No Data をアラート扱い） | チェックアプリが停止している可能性があります |

`delay` / `low_battery` のアラートは、グループが正常に戻るとそのグループの系列がクエリ結果から消える。
Grafana は消えた系列のアラートを数回の評価の後に Normal（解消）にするため、0 を出す方式より解消が数分遅れることがある。
全グループが正常でクエリ結果が空のときは、No Data を OK として扱う。

## 9. リポジトリ構成（案）

```
.
├── README.md
├── docs/
│   ├── design.md               # 本書
│   ├── log-spec.md             # Cloud Logging 出力仕様
│   ├── architecture.md         # GCP構成図（旧版からCloud Monitoring経路を削除）
│   ├── grafana-implementation-notes.md   # 旧版から引き継ぎ・追記
│   └── verification-runbook.md # 4種類それぞれの発火確認手順
├── migrations/                 # 0001_init.sql + apply_migrations.py
├── check_job/                  # Cloud Run Job（Python）
│   ├── checkers/               # missing.py / delay.py / lost.py / low_battery.py
│   └── tests/
├── device_simulator/           # GCE上で常駐（Python + systemd unit）
├── otel-collector/             # logsパイプラインのみ
└── terraform/
```

## 10. 構築の進め方

1. 旧環境を `terraform destroy`（Grafana側含む）
2. 新リポジトリ作成・旧リポジトリから構成を移植し、Cloud Monitoring関連を削除
3. マイグレーション・`check_job`（4チェック＋テスト）・`device_simulator` を実装
4. Terraform で Google Cloud を構築、ログが Loki に届くことを確認し、8.1のクエリを確定
5. Grafana Dashboard / Alert rule を Terraform で作成
6. `verification-runbook.md` に沿って4種類＋チェック失敗・途絶の発火を確認

## 11. 未決・今後の検討事項

- [x] missing チェックが失敗した場合の lost / low_battery の扱い（6.5節：再試行のうえ失敗扱い）
- [x] Lokiでのラベル／フィールドのマッピング確定（8.1節：`{service_name="check-job-<env>"}` ＋ `| json`。
      詳細は `grafana-implementation-notes.md` 5章）
- [ ] 本番規模（数万ユーザー×10秒間隔）での `input_records` の保持期間・パーティショニング
- [ ] 送信間隔の異なるユーザタイプが増えた場合の、ユーザタイプ別閾値
- [ ] 本番環境向けTerraform（可用性・削除保護等の見直し）
