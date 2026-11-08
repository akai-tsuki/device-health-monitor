"""一時ロスト（lost）。docs/design.md 6.3節、docs/log-spec.md 7.4節。

時間帯 [window_start, window_end) に登録されたレコード数が max_records 以下のユーザー（0件含む）を
該当とする。ただし以下は判定対象外（monitored_user_count の母数には含める）。
  - 同じ実行で missing と判定されたユーザー（excluded_user_ids）
  - 一度もデータ登録がないユーザー（missing 側で last_registered_at = null として出る）
"""
from dataclasses import dataclass
from datetime import datetime

from check_job.checkers.base import ActiveUser, CheckOutcome, Detail, build_outcome

CHECK_TYPE = "lost"
STATUS = "lost"


@dataclass(frozen=True)
class UserRecordCount:
    user: ActiveUser
    record_count: int  # 時間帯内のレコード数
    has_any_record: bool  # registered_at < window_end のレコードが1件でもあるか


@dataclass(frozen=True)
class LostData:
    users: list[UserRecordCount]
    excluded_user_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class LostParams:
    max_records: int
    window_start: datetime
    window_end: datetime


def check(data: LostData, params: LostParams, checked_at: datetime) -> CheckOutcome:
    details = []
    for row in data.users:
        if row.user.user_id in data.excluded_user_ids or not row.has_any_record:
            continue
        if row.record_count > params.max_records:
            continue
        details.append(
            Detail(
                user=row.user,
                fields={
                    "window_start": params.window_start,
                    "window_end": params.window_end,
                    "record_count": row.record_count,
                    "threshold_count": params.max_records,
                },
            )
        )
    return build_outcome((r.user for r in data.users), details)
