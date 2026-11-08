"""デバイスシミュレーター（検証・デモ専用、本番のチェック処理とは無関係）。docs/design.md 7章。

常駐プロセスとして SIM_SEND_INTERVAL_SECONDS（デフォルト10秒）ごとに、各仮想デバイス（=ユーザー）の
レコードを input_records へ INSERT する。

  python3 -m device_simulator.main             # 常駐（systemd: device-simulator.service）
  python3 -m device_simulator.main --cleanup   # demo-user-* と関連レコードを全削除して終了

- 起動時に demo-group-1〜N とユーザーを作成する（既存なら維持）。
- SIM_PURGE_INTERVAL_SECONDS（デフォルト1時間）ごとに、SIM_RETENTION_HOURS（デフォルト24時間）より
  前のデモ用レコードを削除する。
- DBエラー時は接続を張り直して継続する（systemd の Restart=always でも保護）。
"""
import argparse
import logging
import random
import signal
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import psycopg2
from psycopg2.extras import execute_values

from device_simulator.behavior import (
    USER_ID_PREFIX,
    USER_TYPE,
    Device,
    battery_voltage,
    build_devices,
    registration_delay_seconds,
    should_send,
)
from device_simulator.config import Config

log = logging.getLogger("device_simulator")
_stop = threading.Event()


def connect(config: Config):
    conn = psycopg2.connect(
        host=config.db_host,
        port=config.db_port,
        dbname=config.db_name,
        user=config.db_user,
        password=config.db_password,
        connect_timeout=10,
        application_name="device_simulator",
    )
    conn.autocommit = True
    return conn


def ensure_users(conn, devices: list[Device]) -> None:
    with conn.cursor() as cur:
        execute_values(
            cur,
            "INSERT INTO users (user_id, group_id, user_type, status) VALUES %s "
            "ON CONFLICT (user_id) DO NOTHING",
            [(d.user_id, d.group_id, USER_TYPE, "active") for d in devices],
        )


def users_with_records(conn, devices: list[Device]) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT user_id FROM input_records WHERE user_id = ANY(%s)",
            ([d.user_id for d in devices],),
        )
        return {row[0] for row in cur.fetchall()}


def build_rows(
    devices: list[Device], now: datetime, has_record: set[str], rng: random.Random
) -> list[tuple]:
    epoch = now.timestamp()
    rows = []
    for d in devices:
        if not should_send(d, epoch, d.user_id in has_record):
            continue
        processed_at = now - timedelta(seconds=registration_delay_seconds(d, rng))
        rows.append((d.user_id, processed_at, now, battery_voltage(d, epoch)))
    return rows


def insert_rows(conn, rows: list[tuple]) -> None:
    if not rows:
        return
    with conn.cursor() as cur:
        execute_values(
            cur,
            "INSERT INTO input_records (user_id, processed_at, registered_at, battery_voltage) VALUES %s",
            rows,
        )


def purge_old_records(conn, retention_hours: int) -> int:
    # missing 役割の「初回の1件」を消すと、再起動時に再送されて一時的に lost に見えてしまうため残す
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM input_records WHERE user_id LIKE %s AND user_id NOT LIKE %s"
            " AND registered_at < now() - make_interval(hours => %s)",
            (USER_ID_PREFIX + "%", USER_ID_PREFIX + "g%-missing-%", retention_hours),
        )
        return cur.rowcount


def cleanup(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM input_records WHERE user_id LIKE %s", (USER_ID_PREFIX + "%",))
        records = cur.rowcount
        cur.execute("DELETE FROM users WHERE user_id LIKE %s", (USER_ID_PREFIX + "%",))
        users = cur.rowcount
    log.info("cleanup: deleted %d users and %d records", users, records)


def run_forever(config: Config) -> None:
    devices = build_devices(config.group_count, config.role_counts)
    log.info(
        "starting: %d devices in %d groups, interval=%ss",
        len(devices), config.group_count, config.send_interval_seconds,
    )
    rng = random.Random()
    conn = None
    has_record: set[str] = set()
    next_purge = 0.0
    interval = config.send_interval_seconds

    while not _stop.is_set():
        # 送信タイミングを時計の interval 境界に揃える（例：:00, :10, :20 ...）
        now_ts = time.time()
        _stop.wait(interval - (now_ts % interval))
        if _stop.is_set():
            break
        try:
            if conn is None or conn.closed:
                conn = connect(config)
                ensure_users(conn, devices)
                has_record = users_with_records(conn, devices)
                log.info("connected; %d devices already have records", len(has_record))
            now = datetime.now(timezone.utc)
            rows = build_rows(devices, now, has_record, rng)
            insert_rows(conn, rows)
            has_record.update(r[0] for r in rows)
            if time.monotonic() >= next_purge:
                deleted = purge_old_records(conn, config.retention_hours)
                log.info("purged %d records older than %dh", deleted, config.retention_hours)
                next_purge = time.monotonic() + config.purge_interval_seconds
        except psycopg2.Error as e:
            log.warning("db error, will reconnect: %s", str(e).strip())
            if conn is not None:
                try:
                    conn.close()
                except psycopg2.Error:
                    pass
            conn = None
    if conn is not None:
        conn.close()
    log.info("stopped")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cleanup", action="store_true", help="demo-user-* と関連レコードを全削除する")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout
    )
    config = Config.from_env()

    if args.cleanup:
        conn = connect(config)
        try:
            cleanup(conn)
        finally:
            conn.close()
        return 0

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: _stop.set())
    run_forever(config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
