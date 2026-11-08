"""各チェックの境界値（閾値ちょうど・NULL・0件・除外）のテスト。docs/design.md 6章。"""
from decimal import Decimal

import pytest

from check_job.checkers import delay, lost, low_battery, missing
from check_job.checkers.base import Summary, Totals
from check_job.tests.helpers import CHECKED_AT, WINDOW_START, ago, user

# ---------------------------------------------------------------------------
# missing
# ---------------------------------------------------------------------------
MISSING_PARAMS = missing.MissingParams(threshold_seconds=900)


def _missing(*rows):
    data = missing.MissingData(
        users=[missing.UserLastRegistration(user=u, last_registered_at=t) for u, t in rows]
    )
    return missing.check(data, MISSING_PARAMS, CHECKED_AT)


def test_missing_never_registered_user_is_missing_with_nulls():
    outcome = _missing((user("u1"), None))

    assert len(outcome.details) == 1
    assert outcome.details[0].fields == {
        "last_registered_at": None,
        "threshold_seconds": 900,
        "elapsed_seconds": None,
    }


def test_missing_exactly_threshold_is_not_missing():
    assert _missing((user("u1"), ago(900))).details == []


def test_missing_just_over_threshold_is_missing_and_elapsed_is_truncated():
    outcome = _missing((user("u1"), ago(900.9)))

    assert [d.user.user_id for d in outcome.details] == ["u1"]
    assert outcome.details[0].fields["elapsed_seconds"] == 900
    assert outcome.details[0].fields["last_registered_at"] == ago(900.9)


def test_missing_recent_user_is_not_missing():
    assert _missing((user("u1"), ago(10))).details == []


# ---------------------------------------------------------------------------
# delay
# ---------------------------------------------------------------------------
DELAY_PARAMS = delay.DelayParams(
    threshold_seconds=20, window_start=WINDOW_START, window_end=CHECKED_AT
)


def _delay(*rows):
    return delay.check(delay.DelayData(users=list(rows)), DELAY_PARAMS, CHECKED_AT)


def test_delay_exactly_threshold_is_delayed():
    row = delay.UserDelay(
        user=user("u1"),
        delayed_record_count=1,
        max_delay_seconds=20.0,
        max_delay_processed_at=ago(80),
        max_delay_registered_at=ago(60),
    )

    outcome = _delay(row)

    assert len(outcome.details) == 1
    assert outcome.details[0].fields == {
        "window_start": WINDOW_START,
        "window_end": CHECKED_AT,
        "threshold_seconds": 20,
        "delayed_record_count": 1,
        "max_delay_seconds": 20,
        "max_delay_processed_at": ago(80),
        "max_delay_registered_at": ago(60),
    }


def test_delay_below_threshold_is_not_delayed():
    row = delay.UserDelay(user=user("u1"), delayed_record_count=1, max_delay_seconds=19.999)
    assert _delay(row).details == []


def test_delay_user_without_delayed_records_is_not_delayed():
    assert _delay(delay.UserDelay(user=user("u1"))).details == []


def test_delay_max_delay_seconds_is_truncated():
    row = delay.UserDelay(user=user("u1"), delayed_record_count=3, max_delay_seconds=41.97)
    assert _delay(row).details[0].fields["max_delay_seconds"] == 41


# ---------------------------------------------------------------------------
# lost
# ---------------------------------------------------------------------------
LOST_PARAMS = lost.LostParams(max_records=8, window_start=WINDOW_START, window_end=CHECKED_AT)


def _lost(rows, excluded=frozenset()):
    return lost.check(lost.LostData(users=list(rows), excluded_user_ids=excluded), LOST_PARAMS, CHECKED_AT)


@pytest.mark.parametrize("count, expected", [(0, True), (8, True), (9, False), (30, False)])
def test_lost_threshold_boundary(count, expected):
    outcome = _lost([lost.UserRecordCount(user("u1"), record_count=count, has_any_record=True)])
    assert (len(outcome.details) == 1) is expected


def test_lost_detail_fields():
    outcome = _lost([lost.UserRecordCount(user("u1"), record_count=6, has_any_record=True)])
    assert outcome.details[0].fields == {
        "window_start": WINDOW_START,
        "window_end": CHECKED_AT,
        "record_count": 6,
        "threshold_count": 8,
    }


def test_lost_excludes_missing_users_but_keeps_them_in_monitored_count():
    outcome = _lost(
        [
            lost.UserRecordCount(user("u1"), record_count=0, has_any_record=True),
            lost.UserRecordCount(user("u2"), record_count=0, has_any_record=True),
        ],
        excluded=frozenset({"u1"}),
    )

    assert [d.user.user_id for d in outcome.details] == ["u2"]
    assert outcome.summaries == [Summary("g1", "default", monitored_user_count=2, anomaly_user_count=1)]


def test_lost_excludes_never_registered_users():
    outcome = _lost([lost.UserRecordCount(user("u1"), record_count=0, has_any_record=False)])
    assert outcome.details == []
    assert outcome.totals.monitored_user_count == 1


# ---------------------------------------------------------------------------
# low_battery
# ---------------------------------------------------------------------------
LB_PARAMS = low_battery.LowBatteryParams(threshold_voltage=Decimal("5.00"))


def _lb(rows, excluded=frozenset()):
    return low_battery.check(
        low_battery.LowBatteryData(users=list(rows), excluded_user_ids=excluded), LB_PARAMS, CHECKED_AT
    )


@pytest.mark.parametrize(
    "voltage, expected", [("5.00", False), ("4.99", True), ("0.00", True), ("10.00", False)]
)
def test_low_battery_threshold_boundary(voltage, expected):
    outcome = _lb([low_battery.UserBattery(user("u1"), Decimal(voltage), ago(10))])
    assert (len(outcome.details) == 1) is expected


def test_low_battery_detail_fields_are_numbers():
    outcome = _lb([low_battery.UserBattery(user("u1"), Decimal("4.85"), ago(10))])
    assert outcome.details[0].fields == {
        "battery_voltage": 4.85,
        "threshold_voltage": 5.0,
        "last_registered_at": ago(10),
    }


def test_low_battery_without_voltage_is_not_judged():
    outcome = _lb([low_battery.UserBattery(user("u1"), None, None)])
    assert outcome.details == []
    assert outcome.totals.monitored_user_count == 1


def test_low_battery_excludes_missing_users():
    outcome = _lb(
        [low_battery.UserBattery(user("u1"), Decimal("1.00"), ago(2000))], excluded=frozenset({"u1"})
    )
    assert outcome.details == []


# ---------------------------------------------------------------------------
# サマリ集計（共通）
# ---------------------------------------------------------------------------
def test_summaries_include_zero_anomaly_groups_and_split_by_user_type():
    outcome = _missing(
        (user("u1", "g1"), None),
        (user("u2", "g1"), ago(10)),
        (user("u3", "g2"), ago(10)),
        (user("u4", "g2", user_type="other"), None),
    )

    assert outcome.summaries == [
        Summary("g1", "default", monitored_user_count=2, anomaly_user_count=1),
        Summary("g2", "default", monitored_user_count=1, anomaly_user_count=0),
        Summary("g2", "other", monitored_user_count=1, anomaly_user_count=1),
    ]
    assert outcome.totals == Totals(group_count=2, monitored_user_count=4, anomaly_user_count=2)


def test_delay_summaries_only_for_groups_with_anomalies():
    def row(uid, group, max_delay):
        return delay.UserDelay(
            user=user(uid, group),
            delayed_record_count=1 if max_delay else 0,
            max_delay_seconds=max_delay,
            max_delay_processed_at=ago(80) if max_delay else None,
            max_delay_registered_at=ago(60) if max_delay else None,
        )

    outcome = _delay(row("u1", "g1", 30.0), row("u2", "g1", None), row("u3", "g2", None))

    # 正常な g2 のサマリは出さない。合計は全ユーザーで集計する
    assert outcome.summaries == [Summary("g1", "default", monitored_user_count=2, anomaly_user_count=1)]
    assert outcome.totals == Totals(group_count=2, monitored_user_count=3, anomaly_user_count=1)


def test_low_battery_summaries_only_for_groups_with_anomalies():
    outcome = _lb(
        [
            low_battery.UserBattery(user("u1", "g1"), Decimal("9.00"), ago(10)),
            low_battery.UserBattery(user("u2", "g2"), Decimal("4.00"), ago(10)),
        ]
    )

    assert outcome.summaries == [Summary("g2", "default", monitored_user_count=1, anomaly_user_count=1)]
    assert outcome.totals == Totals(group_count=2, monitored_user_count=2, anomaly_user_count=1)


def test_low_battery_all_normal_produces_no_summaries():
    outcome = _lb([low_battery.UserBattery(user("u1", "g1"), Decimal("9.00"), ago(10))])

    assert outcome.summaries == []
    assert outcome.totals == Totals(group_count=1, monitored_user_count=1, anomaly_user_count=0)


def test_no_active_users_produces_no_summaries():
    outcome = _missing()
    assert outcome.summaries == []
    assert outcome.totals == Totals(group_count=0, monitored_user_count=0, anomaly_user_count=0)
