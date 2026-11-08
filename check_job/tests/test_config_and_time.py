from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from check_job.config import Config
from check_job.errors import ConfigError
from check_job.timeutil import floor_to_interval, window

BASE_ENV = {"DB_HOST": "h", "DB_NAME": "d", "DB_USER": "u", "DB_PASSWORD": "p", "ENV": "dev"}
JST = timezone(timedelta(hours=9))


def test_config_defaults_match_design_parameters():
    c = Config.from_env(BASE_ENV)
    assert c.tz_name == "Asia/Tokyo"
    assert c.check_interval_minutes == 5
    assert c.missing_threshold_seconds == 900
    assert c.delay_threshold_seconds == 20
    assert c.lost_window_minutes == 5
    assert c.lost_max_records == 8
    assert c.low_battery_threshold_voltage == Decimal("5.00")


@pytest.mark.parametrize(
    "key, value",
    [
        ("TZ_NAME", "Mars/Olympus"),
        ("CHECK_INTERVAL_MINUTES", "7"),
        ("MISSING_THRESHOLD_SECONDS", "0"),
        ("LOW_BATTERY_THRESHOLD_VOLTAGE", "x"),
        ("LOST_MAX_RECORDS", "-1"),
    ],
)
def test_config_invalid_values(key, value):
    with pytest.raises(ConfigError):
        Config.from_env({**BASE_ENV, key: value})


@pytest.mark.parametrize(
    "now, expected",
    [
        (datetime(2026, 11, 7, 17, 20, 3, 500, tzinfo=JST), datetime(2026, 11, 7, 17, 20, tzinfo=JST)),
        (datetime(2026, 11, 7, 17, 24, 59, tzinfo=JST), datetime(2026, 11, 7, 17, 20, tzinfo=JST)),
        (datetime(2026, 11, 7, 17, 25, 0, tzinfo=JST), datetime(2026, 11, 7, 17, 25, tzinfo=JST)),
    ],
)
def test_floor_to_interval(now, expected):
    assert floor_to_interval(now, 5) == expected


def test_window_is_half_open_previous_interval():
    checked_at = datetime(2026, 11, 7, 17, 20, tzinfo=JST)
    assert window(checked_at, 5) == (datetime(2026, 11, 7, 17, 15, tzinfo=JST), checked_at)


def test_floor_requires_aware_datetime():
    with pytest.raises(ValueError):
        floor_to_interval(datetime(2026, 11, 7, 17, 20), 5)
