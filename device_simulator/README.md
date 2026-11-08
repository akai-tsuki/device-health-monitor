# device_simulator — デバイスシミュレーター（検証・デモ専用）

10秒ごとに仮想デバイス（= ユーザー）のレコードを `input_records` へ INSERT する常駐プロセス。
本番のチェック処理とは無関係。仕様は [`../docs/design.md`](../docs/design.md) 7章。

## 動作

- 起動時に `demo-group-1`〜`N` とユーザーを作成（既存なら維持）。役割はユーザーIDに埋め込む：
  `demo-user-g{グループ}-{役割}-{連番}`
- `registered_at = now()`、`processed_at = now() - 遅延`（通常1〜2秒）。送信は時計の10秒境界に揃える。
- バッテリー電圧は 10V → 0V のノコギリ波（周期30〜90分・位相はデバイスごと、小数第2位で丸め）。
- デバイスごとのランダム値は `user_id` をシードに決定するため、再起動しても振る舞いは変わらない。
- 1時間ごとに24時間より前のデモ用レコードを削除（`missing` 役割の初回レコードは残す）。
- DBエラー時は再接続して継続する。

| 役割 | 動き | 主に検知されるチェック |
|---|---|---|
| `normal` | 10秒ごとに送信、遅延1〜2秒 | なし（電圧5V未満の間は low_battery） |
| `delay` | 20%のレコードを遅延20〜60秒で登録 | delay |
| `lost` | 15分周期で4分間送信を停止 | lost（15分に1回） |
| `missing` | 初回に1件だけ登録し、以降送信しない | 登録後15分は lost、その後 missing |
| `never` | 一度も送信しない | missing（`last_registered_at = null`） |

`lost` の停止は5分のチェック境界に揃えて開始する（ずれは最大20秒）。停止を含む時間帯の登録は
約60秒分＝6件となり、閾値8件以下として確実に検知される（停止が2つの時間帯にまたがると、
3〜4分の停止ではどちらの時間帯も8件を超えて検知できないため）。

## 設定（環境変数）

| 変数 | デフォルト |
|---|---|
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` | — / 5432 / — / — |
| `DB_PASSWORD` または `DB_PASSWORD_SECRET` | `DB_PASSWORD_SECRET` は Secret Manager のシークレットID。GCEのメタデータサーバー経由で起動時に取得 |
| `SIM_SEND_INTERVAL_SECONDS` | `10` |
| `SIM_GROUP_COUNT` | `3` |
| `SIM_ROLE_COUNTS` | `normal=3,delay=1,lost=1,missing=1,never=1`（1グループあたり） |
| `SIM_RETENTION_HOURS` | `24` |
| `SIM_PURGE_INTERVAL_SECONDS` | `3600` |

## GCE上での稼働（Terraform `enable_device_simulator = true`）

GCE起動スクリプト（`terraform/scripts/otel_collector_startup.sh`）が、メタデータで渡された
コード一式を `/opt/device-simulator/` に、環境変数を `/etc/device-simulator/env` に配置し、
`device-simulator.service` を有効化・起動する。

```bash
gcloud compute ssh otel-collector-dev --zone=asia-northeast1-a --tunnel-through-iap

sudo systemctl start device-simulator     # 開始
sudo systemctl stop device-simulator      # 停止
sudo journalctl -u device-simulator -f    # ログ

# デモデータの全削除（停止してから）
sudo systemctl stop device-simulator
sudo bash -c 'set -a; . /etc/device-simulator/env; cd /opt/device-simulator; python3 -m device_simulator.main --cleanup'
```

コードを変更した場合は `terraform apply` 後、GCE上で `sudo google_metadata_script_runner startup` を実行する。

## ローカル実行

```bash
pip install -r device_simulator/requirements.txt
export DB_HOST=127.0.0.1 DB_NAME=appdb DB_USER=app_user DB_PASSWORD=...
python -m device_simulator.main
```
