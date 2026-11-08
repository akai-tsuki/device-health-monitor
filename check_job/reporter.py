"""docs/log-spec.md に従ってログJSONを組み立て、標準出力へ1行1件で書き出す。

Cloud Run Job の標準出力は自動で Cloud Logging に取り込まれる（severity / message は
LogEntry のログレベル・表示文として扱われ、それ以外は jsonPayload に入る）。

出力順はチェック種類ごとに detail → summary → check_result（log-spec.md 3章）。
"""
import json
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Any, Optional, TextIO

from check_job.checkers import ANOMALY_STATUS
from check_job.checkers.base import CheckOutcome, Detail, Summary

SCHEMA_VERSION = "1.1"

DETAIL_MESSAGES = {
    "missing": "user data missing",
    "delay": "user data delayed",
    "lost": "user data temporarily lost",
    "low_battery": "user battery low",
}


@dataclass(frozen=True)
class RunContext:
    run_id: str
    checked_at: datetime
    env: str
    tz: tzinfo


def format_time(value: Optional[datetime], tz: tzinfo) -> Optional[str]:
    """ISO 8601（タイムゾーンオフセット付き、秒単位）。None は None のまま。"""
    if value is None:
        return None
    return value.astimezone(tz).isoformat(timespec="seconds")


def _normalize(value: Any, tz: tzinfo) -> Any:
    if isinstance(value, datetime):
        return format_time(value, tz)
    return value


class Reporter:
    def __init__(self, ctx: RunContext, out: TextIO = sys.stdout):
        self._ctx = ctx
        self._out = out

    def _common(self, severity: str, message: str, event_type: str, check_type: str) -> dict:
        return {
            "severity": severity,
            "message": message,
            "schema_version": SCHEMA_VERSION,
            "event_type": event_type,
            "check_type": check_type,
            "record_id": str(uuid.uuid4()),
            "run_id": self._ctx.run_id,
            "checked_at": format_time(self._ctx.checked_at, self._ctx.tz),
            "env": self._ctx.env,
        }

    def _write(self, entry: dict) -> None:
        self._out.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._out.flush()

    # --- 組み立て（テスト用に dict を返す） ---

    def detail_entry(self, check_type: str, detail: Detail) -> dict:
        entry = self._common("WARNING", DETAIL_MESSAGES[check_type], "detail", check_type)
        entry.update(
            {
                "group_id": detail.user.group_id,
                "user_id": detail.user.user_id,
                "user_type": detail.user.user_type,
                "status": ANOMALY_STATUS[check_type],
            }
        )
        entry.update({k: _normalize(v, self._ctx.tz) for k, v in detail.fields.items()})
        return entry

    def summary_entry(self, check_type: str, summary: Summary) -> dict:
        anomalous = summary.anomaly_user_count > 0
        entry = self._common(
            "WARNING" if anomalous else "INFO",
            "group has anomaly users" if anomalous else "group is normal",
            "summary",
            check_type,
        )
        entry.update(
            {
                "group_id": summary.group_id,
                "user_type": summary.user_type,
                "monitored_user_count": summary.monitored_user_count,
                "anomaly_user_count": summary.anomaly_user_count,
                "status": ANOMALY_STATUS[check_type] if anomalous else "normal",
            }
        )
        return entry

    def check_result_entry(
        self,
        check_type: str,
        duration_ms: int,
        outcome: Optional[CheckOutcome] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> dict:
        success = outcome is not None and error_code is None
        entry = self._common(
            "INFO" if success else "ERROR",
            "check completed" if success else "check failed",
            "check_result",
            check_type,
        )
        totals = outcome.totals if success else None
        entry.update(
            {
                "success": success,
                "duration_ms": int(duration_ms),
                "group_count": totals.group_count if totals else None,
                "monitored_user_count": totals.monitored_user_count if totals else None,
                "anomaly_user_count": totals.anomaly_user_count if totals else None,
                "error_code": None if success else error_code,
                "error_message": None if success else error_message,
            }
        )
        return entry

    # --- 出力 ---

    def report_success(self, check_type: str, outcome: CheckOutcome, duration_ms: int) -> None:
        for detail in outcome.details:
            self._write(self.detail_entry(check_type, detail))
        for summary in outcome.summaries:
            self._write(self.summary_entry(check_type, summary))
        # check_result は必ずそのチェック種類の最後に出す
        self._write(self.check_result_entry(check_type, duration_ms, outcome=outcome))

    def report_failure(
        self, check_type: str, duration_ms: int, error_code: str, error_message: str
    ) -> None:
        self._write(
            self.check_result_entry(
                check_type, duration_ms, error_code=error_code, error_message=error_message
            )
        )
