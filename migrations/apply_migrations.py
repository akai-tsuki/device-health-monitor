#!/usr/bin/env python3
"""migrations/ 配下の未適用SQLファイルを、ファイル名の昇順に適用する。

接続先はCloud Run Jobと同じ環境変数名で指定する（terraform/cloud_run_job.tf 参照）:
  DB_HOST, DB_NAME, DB_USER, DB_PASSWORD, DB_PORT（省略時 5432）

適用履歴はDB内の schema_migrations テーブルで管理し、同じファイルは二度適用しない。
"""
import os
import sys
from pathlib import Path

import psycopg2

MIGRATIONS_DIR = Path(__file__).parent
SCHEMA_MIGRATIONS_TABLE = "schema_migrations"


def get_connection():
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        sslmode=os.environ.get("DB_SSLMODE", "prefer"),
    )


def ensure_migrations_table(conn):
    with conn.cursor() as cur:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA_MIGRATIONS_TABLE} (
                version     TEXT PRIMARY KEY,
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    conn.commit()


def applied_versions(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT version FROM {SCHEMA_MIGRATIONS_TABLE}")
        return {row[0] for row in cur.fetchall()}


def pending_migrations(applied: set[str]) -> list[Path]:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    return [f for f in files if f.name not in applied]


def apply_migration(conn, path: Path) -> None:
    sql = path.read_text()
    with conn.cursor() as cur:
        cur.execute(sql)
        cur.execute(
            f"INSERT INTO {SCHEMA_MIGRATIONS_TABLE} (version) VALUES (%s)",
            (path.name,),
        )
    conn.commit()


def main() -> int:
    conn = get_connection()
    try:
        ensure_migrations_table(conn)
        pending = pending_migrations(applied_versions(conn))

        if not pending:
            print("未適用のマイグレーションはありません（最新の状態です）。")
            return 0

        for path in pending:
            print(f"適用中: {path.name}")
            apply_migration(conn, path)
            print(f"完了:   {path.name}")

        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
