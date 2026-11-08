# マイグレーション

`users` / `input_records` テーブルを作成する軽量マイグレーション機構（旧リポジトリから流用）。
専用ツールは使わず、連番のSQLファイルと小さな適用スクリプトのみで構成する。
新リポジトリでは旧データを保全しないため、スキーマを `0001_init.sql` 1本にまとめ直している
（テーブル定義は [`../docs/design.md`](../docs/design.md) 4章）。

- `NNNN_*.sql` — ファイル名の昇順に適用される。適用済みのファイルは内容を変更しない。
- `apply_migrations.py` — 未適用のSQLを順に適用する。履歴は DB 内の `schema_migrations` テーブルで管理。

## 使い方

### 1. 依存パッケージ

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r migrations/requirements.txt
```

### 2. Cloud SQL への接続経路を確保する

Cloud SQL はプライベートIPのみのため、同じVPC内の GCE（`otel-collector-<environment>`）を踏み台にして
IAP 経由の SSH ポートフォワーディングでトンネルする（フォアグラウンドで維持し、別ターミナルで次へ）。

```bash
gcloud compute ssh otel-collector-dev \
  --zone=asia-northeast1-a \
  --tunnel-through-iap \
  -- -L 5432:<cloud_sql_private_ip>:5432 -N
```

`<cloud_sql_private_ip>` は `terraform output cloud_sql_private_ip`。ローカルの5432番が使用中なら
`-L <空きポート>:...` とし、`DB_PORT` も合わせる。

### 3. 実行

```bash
export DB_HOST=127.0.0.1
export DB_NAME=appdb
export DB_USER=app_user
export DB_PASSWORD=$(gcloud secrets versions access latest --secret=db-password-dev)

python3 migrations/apply_migrations.py
```

## テストデータ

継続的なデモデータは [`../device_simulator/`](../device_simulator/) が投入する。
旧リポジトリの `seed/`（一時的なテストデータ）はシミュレーターに置き換えたため移植していない。
