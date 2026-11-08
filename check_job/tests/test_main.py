"""main.py の実行順・失敗の独立性・ログ形式（docs/log-spec.md）のテスト。DBはフェイクを使う。"""
import io
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from check_job.checkers.delay import DelayData, UserDelay
from check_job.checkers.lost import LostData, UserRecordCount
from check_job.checkers.low_battery import LowBatteryData, UserBattery
from check_job.checkers.missing import MissingData, UserLastRegistration
from check_job.errors import DB_CONNECTION_FAILED, DB_QUERY_FAILED, CheckError
from check_job.main import run
from check_job.tests.helpers import CHECKED_AT, ago, user

ENV = {
    "DB_HOST": "localhost",
    "DB_NAME": "appdb",
    "DB_USER": "app_user",
    "DB_PASSWORD": "pw",
    "ENV": "dev",
    "TZ_NAME": "Asia/Tokyo",
    "CLOUD_RUN_EXECUTION": "check-job-dev-x7k2p",
}
# 起動の数秒の遅れ（17:20:03 → checked_at 17:20:00）
NOW = (CHECKED_AT + timedelta(seconds=3)).astimezone(timezone.utc)

COMMON_KEYS = [
    "severity", "message", "schema_version", "event_type", "check_type",
    "record_id", "run_id", "checked_at", "env",
]
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")

U_OK = user("ok", "g1")
U_MISSING = user("gone", "g1")
U_DELAY = user("slow", "g2")
USERS = [U_OK, U_MISSING, U_DELAY]


class FakeRepo:
    def __init__(self, fail=None):
        self.fail = fail or {}
        self.calls = []
        self.closed = False
        self.excluded = {}

    def _maybe_fail(self, name):
        self.calls.append(name)
        err = self.fail.get(name)
        if isinstance(err, list):  # 呼び出しごとに異なる結果
            err = err.pop(0) if err else None
        if err:
            raise err

    def fetch_missing_data(self, checked_at):
        self._maybe_fail("missing")
        assert checked_at == CHECKED_AT
        return MissingData(
            users=[
                UserLastRegistration(U_OK, ago(10)),
                UserLastRegistration(U_MISSING, ago(1000)),
                UserLastRegistration(U_DELAY, ago(5)),
            ]
        )

    def fetch_delay_data(self, ws, we, threshold):
        self._maybe_fail("delay")
        assert (ws, we, threshold) == (CHECKED_AT - timedelta(minutes=5), CHECKED_AT, 20)
        return DelayData(
            users=[
                UserDelay(U_OK),
                UserDelay(U_MISSING),
                UserDelay(U_DELAY, 3, 41.5, ago(178), ago(137)),
            ]
        )

    def fetch_lost_data(self, ws, we, excluded):
        self._maybe_fail("lost")
        self.excluded["lost"] = excluded
        return LostData(
            users=[
                UserRecordCount(U_OK, 30, True),
                UserRecordCount(U_MISSING, 0, True),
                UserRecordCount(U_DELAY, 5, True),
            ],
            excluded_user_ids=excluded,
        )

    def fetch_low_battery_data(self, checked_at, excluded):
        self._maybe_fail("low_battery")
        self.excluded["low_battery"] = excluded
        return LowBatteryData(
            users=[
                UserBattery(U_OK, Decimal("4.85"), ago(10)),
                UserBattery(U_MISSING, Decimal("1.00"), ago(1000)),
                UserBattery(U_DELAY, Decimal("9.10"), ago(5)),
            ],
            excluded_user_ids=excluded,
        )

    def close(self):
        self.closed = True


def _run(repo, environ=ENV):
    out = io.StringIO()
    code = run(environ=environ, now=NOW, out=out, repo_factory=lambda cfg: repo)
    return code, [json.loads(line) for line in out.getvalue().splitlines()]


def _of(logs, check_type, event_type=None):
    return [
        log for log in logs
        if log["check_type"] == check_type and (event_type is None or log["event_type"] == event_type)
    ]


def test_all_checks_succeed_with_spec_compliant_logs():
    repo = FakeRepo()
    code, logs = _run(repo)

    assert code == 0
    assert repo.closed

    # 実行順と、チェック種類ごとの detail → summary → check_result の順序
    order = [(log["check_type"], log["event_type"]) for log in logs]
    assert [ct for ct, _ in order if _ == "check_result"] == ["missing", "delay", "lost", "low_battery"]
    for ct in ("missing", "delay", "lost", "low_battery"):
        events = [e for c, e in order if c == ct]
        assert events[-1] == "check_result"
        assert events == sorted(events, key=["detail", "summary", "check_result"].index)

    for log in logs:
        assert list(log)[: len(COMMON_KEYS)] == COMMON_KEYS
        assert log["schema_version"] == "1.1"
        assert log["run_id"] == "check-job-dev-x7k2p"
        assert log["checked_at"] == "2026-11-07T17:20:00+09:00"
        assert log["env"] == "dev"
        assert UUID_RE.match(log["record_id"])
    assert len({log["record_id"] for log in logs}) == len(logs)


def test_missing_detail_and_summary():
    _, logs = _run(FakeRepo())

    [detail] = _of(logs, "missing", "detail")
    assert detail == {
        **{k: detail[k] for k in ("record_id",)},
        "severity": "WARNING",
        "message": "user data missing",
        "schema_version": "1.1",
        "event_type": "detail",
        "check_type": "missing",
        "run_id": "check-job-dev-x7k2p",
        "checked_at": "2026-11-07T17:20:00+09:00",
        "env": "dev",
        "group_id": "g1",
        "user_id": "gone",
        "user_type": "default",
        "status": "missing",
        "last_registered_at": "2026-11-07T17:03:20+09:00",
        "threshold_seconds": 900,
        "elapsed_seconds": 1000,
    }

    summaries = {s["group_id"]: s for s in _of(logs, "missing", "summary")}
    assert summaries["g1"]["status"] == "missing"
    assert summaries["g1"]["severity"] == "WARNING"
    assert summaries["g1"]["message"] == "group has anomaly users"
    assert (summaries["g1"]["monitored_user_count"], summaries["g1"]["anomaly_user_count"]) == (2, 1)
    # 該当0件のグループも必ず出す
    assert summaries["g2"]["status"] == "normal"
    assert summaries["g2"]["severity"] == "INFO"
    assert summaries["g2"]["message"] == "group is normal"
    assert summaries["g2"]["anomaly_user_count"] == 0


def test_check_result_success_fields():
    _, logs = _run(FakeRepo())

    [result] = _of(logs, "delay", "check_result")
    assert result["severity"] == "INFO"
    assert result["message"] == "check completed"
    assert result["success"] is True
    assert isinstance(result["duration_ms"], int)
    assert (result["group_count"], result["monitored_user_count"], result["anomaly_user_count"]) == (2, 3, 1)
    assert result["error_code"] is None and result["error_message"] is None

    [detail] = _of(logs, "delay", "detail")
    assert detail["status"] == "delayed"
    assert detail["max_delay_seconds"] == 41
    assert detail["window_start"] == "2026-11-07T17:15:00+09:00"
    assert detail["window_end"] == "2026-11-07T17:20:00+09:00"


def test_lost_and_low_battery_exclude_missing_users():
    repo = FakeRepo()
    _, logs = _run(repo)

    assert repo.excluded == {"lost": frozenset({"gone"}), "low_battery": frozenset({"gone"})}
    assert [d["user_id"] for d in _of(logs, "lost", "detail")] == ["slow"]
    [battery] = _of(logs, "low_battery", "detail")
    assert battery["user_id"] == "ok"
    assert battery["battery_voltage"] == 4.85
    assert battery["threshold_voltage"] == 5.0


def test_one_check_failure_does_not_stop_others():
    repo = FakeRepo(fail={"delay": CheckError(DB_QUERY_FAILED, "canceling statement due to statement timeout")})
    code, logs = _run(repo)

    assert code == 1
    assert _of(logs, "delay") == [_of(logs, "delay", "check_result")[0]]  # 失敗時は check_result のみ
    [result] = _of(logs, "delay")
    assert result["severity"] == "ERROR"
    assert result["message"] == "check failed"
    assert result["success"] is False
    assert result["group_count"] is None
    assert result["monitored_user_count"] is None
    assert result["anomaly_user_count"] is None
    assert result["error_code"] == "DB_QUERY_FAILED"
    assert "statement timeout" in result["error_message"]
    for ct in ("missing", "lost", "low_battery"):
        assert _of(logs, ct, "check_result")[0]["success"] is True


def test_unexpected_exception_is_unknown():
    _, logs = _run(FakeRepo(fail={"lost": ZeroDivisionError("division by zero")}))

    [result] = _of(logs, "lost", "check_result")
    assert result["error_code"] == "UNKNOWN"
    assert result["error_message"] == "ZeroDivisionError: division by zero"


def test_missing_failure_is_retried_once_for_exclusion():
    # 1回目（missing チェック本体）だけ失敗し、除外対象を求める再判定は成功する
    repo = FakeRepo(fail={"missing": [CheckError(DB_CONNECTION_FAILED, "timeout"), None]})
    code, logs = _run(repo)

    assert code == 1
    assert _of(logs, "missing", "check_result")[0]["error_code"] == "DB_CONNECTION_FAILED"
    assert _of(logs, "lost", "check_result")[0]["success"] is True
    assert _of(logs, "low_battery", "check_result")[0]["success"] is True
    assert repo.excluded["lost"] == frozenset({"gone"})
    assert repo.calls.count("missing") == 2


def test_missing_failure_fails_lost_and_low_battery_when_retry_fails():
    err = CheckError(DB_CONNECTION_FAILED, "connection to server at 10.10.0.3 timed out")
    repo = FakeRepo(fail={"missing": err, "delay": err})
    code, logs = _run(repo)

    assert code == 1
    for ct in ("lost", "low_battery"):
        [result] = _of(logs, ct)
        assert result["success"] is False
        assert result["error_code"] == "DB_CONNECTION_FAILED"
        assert result["error_message"].startswith("failed to determine missing users for exclusion:")
    # 再判定は1回だけ（lost で失敗した結果を low_battery でも使う）
    assert repo.calls.count("missing") == 2
    assert "lost" not in repo.calls and "low_battery" not in repo.calls


def test_config_error_reports_all_checks_failed():
    environ = {k: v for k, v in ENV.items() if k != "DB_HOST"}
    environ["DELAY_THRESHOLD_SECONDS"] = "abc"
    code, logs = _run(FakeRepo(), environ)

    assert code == 1
    assert [log["check_type"] for log in logs] == ["missing", "delay", "lost", "low_battery"]
    for log in logs:
        assert log["event_type"] == "check_result"
        assert log["success"] is False
        assert log["error_code"] == "CONFIG_INVALID"
        assert "DB_HOST is required" in log["error_message"]
        assert "DELAY_THRESHOLD_SECONDS" in log["error_message"]
        assert log["checked_at"] == "2026-11-07T17:20:00+09:00"


def test_local_run_uses_uuid_run_id():
    environ = {k: v for k, v in ENV.items() if k != "CLOUD_RUN_EXECUTION"}
    _, logs = _run(FakeRepo(), environ)

    run_ids = {log["run_id"] for log in logs}
    assert len(run_ids) == 1
    assert UUID_RE.match(run_ids.pop())
