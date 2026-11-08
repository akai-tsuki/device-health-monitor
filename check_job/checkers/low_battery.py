"""バッテリー低下（low_battery）。docs/design.md 6.4節、docs/log-spec.md 7.5節。

battery_voltage を持つ最新レコード（registered_at < checked_at の最大）の電圧が
threshold_voltage 未満のユーザーを該当とする（閾値ちょうどは正常）。
同じ実行で missing と判定されたユーザーは、古い電圧で判定しないため対象外。
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

from check_job.checkers.base import ActiveUser, CheckOutcome, Detail, build_outcome

CHECK_TYPE = "low_battery"
STATUS = "low_battery"


@dataclass(frozen=True)
class UserBattery:
    user: ActiveUser
    battery_voltage: Optional[Decimal]  # 電圧を持つ最新レコードの値。なければ None
    registered_at: Optional[datetime]  # そのレコードの registered_at


@dataclass(frozen=True)
class LowBatteryData:
    users: list[UserBattery]
    excluded_user_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class LowBatteryParams:
    threshold_voltage: Decimal


def check(data: LowBatteryData, params: LowBatteryParams, checked_at: datetime) -> CheckOutcome:
    details = []
    for row in data.users:
        if row.user.user_id in data.excluded_user_ids or row.battery_voltage is None:
            continue
        voltage = Decimal(row.battery_voltage)
        if voltage >= params.threshold_voltage:
            continue
        details.append(
            Detail(
                user=row.user,
                fields={
                    "battery_voltage": float(round(voltage, 2)),
                    "threshold_voltage": float(round(params.threshold_voltage, 2)),
                    "last_registered_at": row.registered_at,
                },
            )
        )
    return build_outcome((r.user for r in data.users), details, include_normal_summaries=False)
