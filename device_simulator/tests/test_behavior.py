import random
from datetime import datetime, timezone

import pytest

from device_simulator.behavior import (
    LOST_ALIGN_SECONDS,
    LOST_CYCLE_SECONDS,
    Device,
    battery_voltage,
    build_devices,
    is_lost_paused,
    parse_role,
    parse_role_counts,
    registration_delay_seconds,
    should_send,
)
from device_simulator.main import build_rows

T0 = datetime(2026, 11, 7, 8, 0, tzinfo=timezone.utc).timestamp()  # 5分境界


def test_user_id_embeds_role_and_is_parseable():
    d = Device.create(2, "delay", 1)
    assert d.user_id == "demo-user-g2-delay-01"
    assert d.group_id == "demo-group-2"
    assert parse_role(d.user_id) == "delay"
    assert parse_role("someone-else") is None


def test_device_parameters_are_deterministic_per_user_id():
    assert Device.create(1, "normal", 1) == Device.create(1, "normal", 1)


def test_build_devices_and_role_counts():
    counts = parse_role_counts("normal=2, lost=1,never=0")
    devices = build_devices(2, counts)
    assert [d.user_id for d in devices] == [
        "demo-user-g1-normal-01", "demo-user-g1-normal-02", "demo-user-g1-lost-01",
        "demo-user-g2-normal-01", "demo-user-g2-normal-02", "demo-user-g2-lost-01",
    ]
    with pytest.raises(ValueError):
        parse_role_counts("zombie=1")


def test_battery_is_sawtooth_between_10_and_0_rounded():
    d = Device.create(1, "normal", 1)
    start = -d.battery_phase_seconds  # 周期の先頭
    assert battery_voltage(d, start) == 10.0
    assert battery_voltage(d, start + d.battery_period_seconds / 2) == 5.0
    assert 0 <= battery_voltage(d, start + d.battery_period_seconds - 1) < 0.01
    assert battery_voltage(d, start + d.battery_period_seconds) == 10.0
    samples = [battery_voltage(d, T0 + s) for s in range(0, 7200, 10)]
    assert all(0 <= v <= 10 and round(v, 2) == v for v in samples)
    assert min(samples) < 5 < max(samples)


def _records_in_window(d, window_start):
    sends = [should_send(d, window_start + s, True) for s in range(0, 300, 10)]
    return sum(sends)


def test_lost_device_is_detectable_in_one_window_per_cycle_and_never_missing():
    """停止を含む5分の時間帯は8件以下、それ以外の時間帯は30件（docs/design.md 6.3節）。"""
    for serial in range(1, 30):
        d = Device.create(1, "lost", serial)
        counts = [_records_in_window(d, T0 + w * LOST_ALIGN_SECONDS) for w in range(3)]
        assert sorted(counts)[0] <= 8, (d, counts)
        assert sorted(counts)[1:] == [30, 30], (d, counts)
        # 送信が止まる最長時間は15分（missing の閾値）より十分短い
        paused = [is_lost_paused(d, T0 + s) for s in range(0, LOST_CYCLE_SECONDS, 10)]
        assert 0 < sum(paused) * 10 <= 4 * 60


def test_should_send_by_role():
    assert should_send(Device.create(1, "normal", 1), T0, True)
    assert should_send(Device.create(1, "missing", 1), T0, has_any_record=False)
    assert not should_send(Device.create(1, "missing", 1), T0, has_any_record=True)
    assert not should_send(Device.create(1, "never", 1), T0, has_any_record=False)


def test_registration_delay_ranges():
    rng = random.Random(0)
    normal = [registration_delay_seconds(Device.create(1, "normal", 1), rng) for _ in range(500)]
    assert all(1 <= x <= 2 for x in normal)
    delayed = [registration_delay_seconds(Device.create(1, "delay", 1), rng) for _ in range(2000)]
    big = [x for x in delayed if x >= 20]
    assert all(20 <= x <= 60 for x in big)
    assert 0.1 < len(big) / len(delayed) < 0.3


def test_build_rows_sets_processed_before_registered():
    now = datetime(2026, 11, 7, 8, 0, 10, tzinfo=timezone.utc)
    devices = build_devices(1, {"normal": 1, "never": 1, "missing": 1})
    rows = build_rows(devices, now, has_record={"demo-user-g1-missing-01"}, rng=random.Random(1))
    assert [r[0] for r in rows] == ["demo-user-g1-normal-01"]
    user_id, processed_at, registered_at, voltage = rows[0]
    assert registered_at == now
    assert 1 <= (registered_at - processed_at).total_seconds() <= 2
    assert 0 <= voltage <= 10
