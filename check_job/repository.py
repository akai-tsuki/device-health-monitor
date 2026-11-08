"""DBアクセス層。チェック種類ごとに、checkers/ が必要とするデータを取得する。

- 接続は初回クエリ時に確立し、1回の実行内で使い回す（失敗時は次のクエリで再接続を試みる）
- 読み取り専用のため autocommit で実行し、1クエリの失敗が後続のチェックに波及しないようにする
- 例外は errors.CheckError（DB_CONNECTION_FAILED / DB_QUERY_FAILED）に変換して送出する

判定はすべて registered_at 基準で、checked_at より前（registered_at < checked_at）のデータのみを使う
（docs/design.md 5章）。
"""
from datetime import datetime
from decimal import Decimal

import psycopg2

from check_job.checkers.base import ActiveUser
from check_job.checkers.delay import DelayData, UserDelay
from check_job.checkers.lost import LostData, UserRecordCount
from check_job.checkers.low_battery import LowBatteryData, UserBattery
from check_job.checkers.missing import MissingData, UserLastRegistration
from check_job.config import Config
from check_job.errors import DB_CONNECTION_FAILED, DB_QUERY_FAILED, CheckError

CONNECT_TIMEOUT_SECONDS = 10
STATEMENT_TIMEOUT_MS = 60_000

# 6.1 データ欠損：各ユーザーの最新 registered_at（checked_at より前）
_MISSING_QUERY = """
    SELECT u.user_id, u.group_id, u.user_type, latest.registered_at
    FROM users u
    LEFT JOIN LATERAL (
        SELECT ir.registered_at
        FROM input_records ir
        WHERE ir.user_id = u.user_id
          AND ir.registered_at < %(checked_at)s
        ORDER BY ir.registered_at DESC
        LIMIT 1
    ) latest ON true
    WHERE u.status = 'active'
"""

# 6.2 データ遅延：時間帯内の閾値以上の遅延レコードをユーザー単位に集約し、最大遅延レコードを1件取る
_DELAY_QUERY = """
    WITH active AS (
        SELECT user_id, group_id, user_type FROM users WHERE status = 'active'
    ),
    delayed AS (
        SELECT ir.user_id,
               ir.processed_at,
               ir.registered_at,
               EXTRACT(EPOCH FROM ir.registered_at - ir.processed_at) AS delay_seconds,
               COUNT(*) OVER (PARTITION BY ir.user_id) AS delayed_record_count,
               ROW_NUMBER() OVER (
                   PARTITION BY ir.user_id
                   ORDER BY ir.registered_at - ir.processed_at DESC, ir.registered_at DESC
               ) AS rn
        FROM input_records ir
        JOIN active a ON a.user_id = ir.user_id
        WHERE ir.registered_at >= %(window_start)s
          AND ir.registered_at <  %(window_end)s
          AND ir.registered_at - ir.processed_at >= make_interval(secs => %(threshold)s)
    )
    SELECT a.user_id, a.group_id, a.user_type,
           d.delayed_record_count, d.delay_seconds, d.processed_at, d.registered_at
    FROM active a
    LEFT JOIN delayed d ON d.user_id = a.user_id AND d.rn = 1
"""

# 6.3 一時ロスト：時間帯内のレコード数（0件含む）と、時間帯終了までに1件でも登録があるか
_LOST_QUERY = """
    SELECT u.user_id, u.group_id, u.user_type,
           COALESCE(c.record_count, 0) AS record_count,
           EXISTS (
               SELECT 1 FROM input_records ir
               WHERE ir.user_id = u.user_id AND ir.registered_at < %(window_end)s
           ) AS has_any_record
    FROM users u
    LEFT JOIN (
        SELECT user_id, COUNT(*) AS record_count
        FROM input_records
        WHERE registered_at >= %(window_start)s
          AND registered_at <  %(window_end)s
        GROUP BY user_id
    ) c ON c.user_id = u.user_id
    WHERE u.status = 'active'
"""

# 6.4 バッテリー低下：電圧を持つ最新レコード（checked_at より前）
_LOW_BATTERY_QUERY = """
    SELECT u.user_id, u.group_id, u.user_type, latest.battery_voltage, latest.registered_at
    FROM users u
    LEFT JOIN LATERAL (
        SELECT ir.battery_voltage, ir.registered_at
        FROM input_records ir
        WHERE ir.user_id = u.user_id
          AND ir.registered_at < %(checked_at)s
          AND ir.battery_voltage IS NOT NULL
        ORDER BY ir.registered_at DESC
        LIMIT 1
    ) latest ON true
    WHERE u.status = 'active'
"""


class Repository:
    def __init__(self, config: Config):
        self._config = config
        self._conn = None

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def _connection(self):
        if self._conn is not None and not self._conn.closed:
            return self._conn
        try:
            conn = psycopg2.connect(
                host=self._config.db_host,
                port=self._config.db_port,
                dbname=self._config.db_name,
                user=self._config.db_user,
                password=self._config.db_password,
                connect_timeout=CONNECT_TIMEOUT_SECONDS,
                options=f"-c statement_timeout={STATEMENT_TIMEOUT_MS}",
                application_name="check_job",
            )
            conn.autocommit = True
        except psycopg2.Error as e:
            raise CheckError(DB_CONNECTION_FAILED, str(e)) from e
        self._conn = conn
        return conn

    def _fetchall(self, sql: str, params: dict) -> list[tuple]:
        conn = self._connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()
        except psycopg2.OperationalError as e:
            # 接続断の可能性があるため、次のクエリでは再接続させる
            self.close()
            raise CheckError(DB_QUERY_FAILED, str(e)) from e
        except psycopg2.Error as e:
            raise CheckError(DB_QUERY_FAILED, str(e)) from e

    def fetch_missing_data(self, checked_at: datetime) -> MissingData:
        rows = self._fetchall(_MISSING_QUERY, {"checked_at": checked_at})
        return MissingData(
            users=[
                UserLastRegistration(user=ActiveUser(uid, gid, utype), last_registered_at=last)
                for uid, gid, utype, last in rows
            ]
        )

    def fetch_delay_data(
        self, window_start: datetime, window_end: datetime, threshold_seconds: int
    ) -> DelayData:
        rows = self._fetchall(
            _DELAY_QUERY,
            {"window_start": window_start, "window_end": window_end, "threshold": threshold_seconds},
        )
        return DelayData(
            users=[
                UserDelay(
                    user=ActiveUser(uid, gid, utype),
                    delayed_record_count=int(count or 0),
                    max_delay_seconds=float(delay) if delay is not None else None,
                    max_delay_processed_at=processed_at,
                    max_delay_registered_at=registered_at,
                )
                for uid, gid, utype, count, delay, processed_at, registered_at in rows
            ]
        )

    def fetch_lost_data(
        self, window_start: datetime, window_end: datetime, excluded_user_ids: frozenset[str]
    ) -> LostData:
        rows = self._fetchall(
            _LOST_QUERY, {"window_start": window_start, "window_end": window_end}
        )
        return LostData(
            users=[
                UserRecordCount(
                    user=ActiveUser(uid, gid, utype),
                    record_count=int(count),
                    has_any_record=bool(has_any),
                )
                for uid, gid, utype, count, has_any in rows
            ],
            excluded_user_ids=excluded_user_ids,
        )

    def fetch_low_battery_data(
        self, checked_at: datetime, excluded_user_ids: frozenset[str]
    ) -> LowBatteryData:
        rows = self._fetchall(_LOW_BATTERY_QUERY, {"checked_at": checked_at})
        return LowBatteryData(
            users=[
                UserBattery(
                    user=ActiveUser(uid, gid, utype),
                    battery_voltage=Decimal(voltage) if voltage is not None else None,
                    registered_at=registered_at,
                )
                for uid, gid, utype, voltage, registered_at in rows
            ],
            excluded_user_ids=excluded_user_ids,
        )
