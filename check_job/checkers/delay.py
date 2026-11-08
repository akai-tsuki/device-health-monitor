"""データ遅延（delay）。docs/design.md 6.2節、docs/log-spec.md 7.3節。

時間帯 [window_start, window_end) に registered_at - processed_at >= threshold_seconds の
レコードが1件以上あるユーザーを該当とする。

DB側（repository.py）で閾値以上のレコードに絞り込んでユーザー単位に集約したものを受け取る
（全レコードを転送しないため）。ここでも閾値を再確認し、境界の扱いをテスト可能にしている。
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from check_job.checkers.base import ActiveUser, CheckOutcome, Detail, build_outcome

CHECK_TYPE = "delay"
STATUS = "delayed"


@dataclass(frozen=True)
class UserDelay:
    user: ActiveUser
    delayed_record_count: int = 0
    max_delay_seconds: Optional[float] = None  # 遅延レコードがなければ None
    max_delay_processed_at: Optional[datetime] = None
    max_delay_registered_at: Optional[datetime] = None


@dataclass(frozen=True)
class DelayData:
    users: list[UserDelay]


@dataclass(frozen=True)
class DelayParams:
    threshold_seconds: int
    window_start: datetime
    window_end: datetime


def check(data: DelayData, params: DelayParams, checked_at: datetime) -> CheckOutcome:
    details = []
    for row in data.users:
        if (
            row.delayed_record_count < 1
            or row.max_delay_seconds is None
            or row.max_delay_seconds < params.threshold_seconds
        ):
            continue
        details.append(
            Detail(
                user=row.user,
                fields={
                    "window_start": params.window_start,
                    "window_end": params.window_end,
                    "threshold_seconds": params.threshold_seconds,
                    "delayed_record_count": row.delayed_record_count,
                    "max_delay_seconds": int(row.max_delay_seconds),  # 切り捨て
                    "max_delay_processed_at": row.max_delay_processed_at,
                    "max_delay_registered_at": row.max_delay_registered_at,
                },
            )
        )
    return build_outcome((r.user for r in data.users), details, include_normal_summaries=False)
