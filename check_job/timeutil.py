"""チェックの時間基準。docs/design.md 5章を参照。"""
from datetime import datetime, timedelta


def floor_to_interval(now: datetime, interval_minutes: int) -> datetime:
    """起動時刻を interval_minutes 境界に切り捨てる（例：17:20:03 → 17:20:00）。

    Cloud Scheduler は */N で起動するため、起動の数秒の遅れを吸収できる。
    interval_minutes は 60 の約数であること（config.py で検証済み）。
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.replace(
        minute=now.minute - now.minute % interval_minutes, second=0, microsecond=0
    )


def window(checked_at: datetime, minutes: int) -> tuple[datetime, datetime]:
    """判定対象時間帯 [checked_at - minutes, checked_at) を返す。"""
    return checked_at - timedelta(minutes=minutes), checked_at
