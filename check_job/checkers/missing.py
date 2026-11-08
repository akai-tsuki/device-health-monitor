"""データ欠損（missing）。docs/design.md 6.1節、docs/log-spec.md 7.2節。

last_registered_at が NULL（一度も登録なし）
または checked_at - last_registered_at > threshold_seconds のユーザーを該当とする。
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from check_job.checkers.base import ActiveUser, CheckOutcome, Detail, build_outcome

CHECK_TYPE = "missing"
STATUS = "missing"


@dataclass(frozen=True)
class UserLastRegistration:
    user: ActiveUser
    last_registered_at: Optional[datetime]  # registered_at < checked_at の最大値


@dataclass(frozen=True)
class MissingData:
    users: list[UserLastRegistration]


@dataclass(frozen=True)
class MissingParams:
    threshold_seconds: int


def check(data: MissingData, params: MissingParams, checked_at: datetime) -> CheckOutcome:
    details = []
    for row in data.users:
        if row.last_registered_at is None:
            elapsed_seconds = None
        else:
            elapsed = (checked_at - row.last_registered_at).total_seconds()
            if elapsed <= params.threshold_seconds:
                continue
            elapsed_seconds = int(elapsed)  # 閾値超過のため正の値。切り捨て
        details.append(
            Detail(
                user=row.user,
                fields={
                    "last_registered_at": row.last_registered_at,
                    "threshold_seconds": params.threshold_seconds,
                    "elapsed_seconds": elapsed_seconds,
                },
            )
        )
    return build_outcome((r.user for r in data.users), details)
