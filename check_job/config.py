"""環境変数からの設定読み込み。terraform/cloud_run_job.tf の env と対応する。

閾値のデフォルト値は docs/design.md 2章のパラメータ表に合わせている。
"""
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from check_job.errors import ConfigError

DEFAULT_TZ_NAME = "Asia/Tokyo"


@dataclass(frozen=True)
class Config:
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    env: str
    tz_name: str
    check_interval_minutes: int
    missing_threshold_seconds: int
    delay_threshold_seconds: int
    lost_window_minutes: int
    lost_max_records: int
    low_battery_threshold_voltage: Decimal

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.tz_name)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> "Config":
        errors: list[str] = []

        def required(name: str) -> str:
            value = environ.get(name, "")
            if not value:
                errors.append(f"{name} is required")
            return value

        def integer(name: str, default: int, minimum: int = 1) -> int:
            raw = environ.get(name, str(default))
            try:
                value = int(raw)
            except ValueError:
                errors.append(f"{name} must be an integer: {raw!r}")
                return default
            if value < minimum:
                errors.append(f"{name} must be >= {minimum}: {value}")
            return value

        def decimal(name: str, default: str) -> Decimal:
            raw = environ.get(name, default)
            try:
                value = Decimal(raw)
            except InvalidOperation:
                errors.append(f"{name} must be a number: {raw!r}")
                return Decimal(default)
            if not value.is_finite() or value <= 0:
                errors.append(f"{name} must be a positive number: {raw!r}")
            return value

        tz_name = environ.get("TZ_NAME", DEFAULT_TZ_NAME)
        try:
            ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            errors.append(f"TZ_NAME is not a valid IANA timezone: {tz_name!r}")

        check_interval = integer("CHECK_INTERVAL_MINUTES", 5)
        if check_interval >= 1 and 60 % check_interval != 0:
            # checked_at を「毎時0分から始まる区切り」に揃えるため、60の約数に限定する
            errors.append(f"CHECK_INTERVAL_MINUTES must divide 60: {check_interval}")

        config = cls(
            db_host=required("DB_HOST"),
            db_port=integer("DB_PORT", 5432),
            db_name=required("DB_NAME"),
            db_user=required("DB_USER"),
            db_password=required("DB_PASSWORD"),
            env=required("ENV"),
            tz_name=tz_name,
            check_interval_minutes=check_interval,
            missing_threshold_seconds=integer("MISSING_THRESHOLD_SECONDS", 900),
            delay_threshold_seconds=integer("DELAY_THRESHOLD_SECONDS", 20),
            lost_window_minutes=integer("LOST_WINDOW_MINUTES", 5),
            lost_max_records=integer("LOST_MAX_RECORDS", 8, minimum=0),
            low_battery_threshold_voltage=decimal("LOW_BATTERY_THRESHOLD_VOLTAGE", "5.00"),
        )
        if errors:
            raise ConfigError("; ".join(errors))
        return config
