from datetime import datetime, timedelta, timezone

from check_job.checkers.base import ActiveUser

JST = timezone(timedelta(hours=9))
CHECKED_AT = datetime(2026, 11, 7, 17, 20, 0, tzinfo=JST)
WINDOW_START = CHECKED_AT - timedelta(minutes=5)


def user(user_id: str, group_id: str = "g1", user_type: str = "default") -> ActiveUser:
    return ActiveUser(user_id=user_id, group_id=group_id, user_type=user_type)


def ago(seconds: float) -> datetime:
    return CHECKED_AT - timedelta(seconds=seconds)
