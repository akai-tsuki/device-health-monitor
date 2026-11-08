# check_job — チェック処理本体（Cloud Run Job）

5分ごとに Cloud Scheduler から起動され、4種類のチェック（missing → delay → lost → low_battery）を
行って、[`../docs/log-spec.md`](../docs/log-spec.md) 形式の JSON を標準出力へ書き出す。
判定ロジックは [`../docs/design.md`](../docs/design.md) 5〜6章を参照。

## 構成

| モジュール | 役割 |
|---|---|
| `config.py` | 環境変数の読み込み・検証（不備は `CONFIG_INVALID`） |
| `timeutil.py` | `checked_at`（起動時刻の5分境界切り捨て）と判定時間帯 `[checked_at - 5分, checked_at)` |
| `repository.py` | DBアクセス。チェック種類ごとのデータ取得（DBエラーは `DB_CONNECTION_FAILED` / `DB_QUERY_FAILED`） |
| `checkers/` | チェック種類ごとの純粋ロジック（`missing.py` / `delay.py` / `lost.py` / `low_battery.py`）。共通インターフェース `check(data, params, checked_at) -> CheckOutcome(details, summaries, totals)` |
| `reporter.py` | ログJSONの組み立てと出力（detail → summary → check_result の順） |
| `main.py` | 実行順の制御。各チェックは独立に try/except し、失敗は `success=false` の check_result として出力 |

### missing が失敗した場合の lost / low_battery（design.md 6.5節の決定事項）

lost / low_battery は除外対象（missing 該当ユーザー）を求めるため、missing の判定を1回だけ再試行する。
再試行も失敗した場合、lost / low_battery は **失敗扱い**（`success=false`、`error_code` は再試行時の
エラー分類、`error_message` は `failed to determine missing users for exclusion: ...`）とする。
「除外なしで判定」すると missing ユーザーが lost / low_battery として誤って計上されるため採らない。

### 終了コード

いずれかのチェックが失敗すると終了コード1で終わる（ログ出力は完了済み）。同じ `run_id` で
ログが二重に出ないよう、Cloud Run Job のリトライは0回にしている。

## 環境変数

| 変数 | デフォルト | 説明 |
|---|---|---|
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` | `DB_PORT=5432` 以外必須 | 接続先 |
| `ENV` | 必須 | ログの `env` |
| `TZ_NAME` | `Asia/Tokyo` | ログの日時表記のタイムゾーン |
| `CHECK_INTERVAL_MINUTES` | `5` | `checked_at` の切り捨て単位・遅延の判定時間帯（60の約数） |
| `MISSING_THRESHOLD_SECONDS` | `900` | データ欠損閾値（超過で該当） |
| `DELAY_THRESHOLD_SECONDS` | `20` | データ遅延閾値（以上で該当） |
| `LOST_WINDOW_MINUTES` | `5` | 一時ロストの集計時間帯（チェック間隔と同じにする） |
| `LOST_MAX_RECORDS` | `8` | 一時ロスト閾値（以下で該当） |
| `LOW_BATTERY_THRESHOLD_VOLTAGE` | `5.00` | バッテリー低下閾値（未満で該当） |
| `CLOUD_RUN_EXECUTION` | Cloud Run が設定 | `run_id`。未設定（ローカル）なら UUID |

## テスト

```bash
pip install -r check_job/requirements-dev.txt
pytest check_job/tests/
```

`checkers/` は境界値（閾値ちょうど・NULL・0件・除外）を、`test_main.py` はフェイクDBで
実行順・失敗の独立性・ログ形式を確認する。

## ローカル実行（DB接続あり）

```bash
export DB_HOST=127.0.0.1 DB_NAME=appdb DB_USER=app_user ENV=dev
export DB_PASSWORD=$(gcloud secrets versions access latest --secret=db-password-dev)
python -m check_job.main
```

Cloud SQL への接続経路は [`../migrations/README.md`](../migrations/README.md) を参照。

## イメージのビルド・push

```bash
docker build -f check_job/Dockerfile -t check-job .
docker tag check-job <artifact_registry_repo_url>/check-job:latest
docker push <artifact_registry_repo_url>/check-job:latest
```

`<artifact_registry_repo_url>` は `terraform output artifact_registry_repo_url`。
