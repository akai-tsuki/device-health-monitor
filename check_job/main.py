"""Cloud Run Job のエントリポイント。docs/design.md 5〜6章、docs/log-spec.md を参照。

1回の実行で missing → delay → lost → low_battery の順に4種類のチェックを行い、
すべて同じ checked_at（起動時刻を CHECK_INTERVAL_MINUTES 境界に切り捨てた時刻）と run_id を使う。

- 各チェック種類は独立に try/except し、1種類の失敗で他を止めない。
- lost / low_battery は同じ実行の missing 該当ユーザーを除外する。missing チェックが失敗した場合は、
  除外対象を求めるために missing の判定（データ取得＋判定）をもう一度だけ試み、それも失敗したら
  lost / low_battery も失敗扱い（success=false、error_message に除外対象の取得失敗である旨を記載）にする。
  「除外なしで判定」すると missing ユーザーが lost / low_battery として誤って計上されるため採らない
  （docs/design.md 6.5節・11章の未決事項に対する決定）。
- いずれかのチェックが失敗した場合は終了コード1で終了する（ログ出力自体は完了している）。
"""
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, Mapping, Optional, TextIO
from zoneinfo import ZoneInfo

from check_job.checkers import delay, lost, low_battery, missing
from check_job.checkers.base import CheckOutcome
from check_job.config import DEFAULT_TZ_NAME, Config
from check_job.errors import CONFIG_INVALID, CheckError, ConfigError, classify
from check_job.reporter import Reporter, RunContext
from check_job.repository import Repository
from check_job.timeutil import floor_to_interval, window


def _run_id(environ: Mapping[str, str]) -> str:
    return environ.get("CLOUD_RUN_EXECUTION") or str(uuid.uuid4())


class CheckRunner:
    def __init__(self, config: Config, repo, reporter: Reporter, checked_at: datetime):
        self._config = config
        self._repo = repo
        self._reporter = reporter
        self._checked_at = checked_at
        self._missing_outcome: Optional[CheckOutcome] = None
        self._missing_retry_error: Optional[CheckError] = None

    # --- 各チェック（データ取得＋判定） ---

    def _check_missing(self) -> CheckOutcome:
        data = self._repo.fetch_missing_data(self._checked_at)
        params = missing.MissingParams(threshold_seconds=self._config.missing_threshold_seconds)
        return missing.check(data, params, self._checked_at)

    def _missing_user_ids(self) -> frozenset[str]:
        """lost / low_battery の除外対象。missing が失敗していた場合は1回だけ再判定する。"""
        if self._missing_outcome is None and self._missing_retry_error is None:
            try:
                self._missing_outcome = self._check_missing()
            except Exception as e:  # noqa: BLE001 - 分類して呼び出し元へ
                code, message = classify(e)
                self._missing_retry_error = CheckError(
                    code, f"failed to determine missing users for exclusion: {message}"
                )
        if self._missing_retry_error is not None:
            raise self._missing_retry_error
        return self._missing_outcome.anomaly_user_ids

    def _check_delay(self) -> CheckOutcome:
        ws, we = window(self._checked_at, self._config.check_interval_minutes)
        data = self._repo.fetch_delay_data(ws, we, self._config.delay_threshold_seconds)
        params = delay.DelayParams(
            threshold_seconds=self._config.delay_threshold_seconds, window_start=ws, window_end=we
        )
        return delay.check(data, params, self._checked_at)

    def _check_lost(self) -> CheckOutcome:
        excluded = self._missing_user_ids()
        ws, we = window(self._checked_at, self._config.lost_window_minutes)
        data = self._repo.fetch_lost_data(ws, we, excluded)
        params = lost.LostParams(
            max_records=self._config.lost_max_records, window_start=ws, window_end=we
        )
        return lost.check(data, params, self._checked_at)

    def _check_low_battery(self) -> CheckOutcome:
        excluded = self._missing_user_ids()
        data = self._repo.fetch_low_battery_data(self._checked_at, excluded)
        params = low_battery.LowBatteryParams(
            threshold_voltage=self._config.low_battery_threshold_voltage
        )
        return low_battery.check(data, params, self._checked_at)

    # --- 実行・出力 ---

    def _run_one(self, check_type: str, fn: Callable[[], CheckOutcome]) -> bool:
        started = time.monotonic()
        try:
            outcome = fn()
        except Exception as e:  # noqa: BLE001 - 1種類の失敗で他を止めない
            duration_ms = int((time.monotonic() - started) * 1000)
            code, message = classify(e)
            self._reporter.report_failure(check_type, duration_ms, code, message)
            return False
        duration_ms = int((time.monotonic() - started) * 1000)
        if check_type == missing.CHECK_TYPE:
            self._missing_outcome = outcome
        self._reporter.report_success(check_type, outcome, duration_ms)
        return True

    def run_all(self) -> bool:
        results = [
            self._run_one(missing.CHECK_TYPE, self._check_missing),
            self._run_one(delay.CHECK_TYPE, self._check_delay),
            self._run_one(lost.CHECK_TYPE, self._check_lost),
            self._run_one(low_battery.CHECK_TYPE, self._check_low_battery),
        ]
        return all(results)


def _report_config_error(
    error: ConfigError, environ: Mapping[str, str], now: datetime, out: TextIO
) -> None:
    """設定不備の場合も、4種類すべてについて success=false の check_result を出す。"""
    try:
        tz = ZoneInfo(environ.get("TZ_NAME", DEFAULT_TZ_NAME))
    except Exception:  # noqa: BLE001 - TZ_NAME 自体が不正な場合
        tz = timezone.utc
    try:
        interval = int(environ.get("CHECK_INTERVAL_MINUTES", "5"))
        if interval < 1 or 60 % interval != 0:
            raise ValueError
    except ValueError:
        interval = 5
    ctx = RunContext(
        run_id=_run_id(environ),
        checked_at=floor_to_interval(now.astimezone(tz), interval),
        env=environ.get("ENV") or "unknown",
        tz=tz,
    )
    reporter = Reporter(ctx, out)
    _, message = classify(error)
    for check_type in (missing.CHECK_TYPE, delay.CHECK_TYPE, lost.CHECK_TYPE, low_battery.CHECK_TYPE):
        reporter.report_failure(check_type, 0, CONFIG_INVALID, message)


def run(
    environ: Mapping[str, str] = os.environ,
    now: Optional[datetime] = None,
    out: TextIO = sys.stdout,
    repo_factory: Callable[[Config], object] = Repository,
) -> int:
    now = now or datetime.now(timezone.utc)
    try:
        config = Config.from_env(environ)
    except ConfigError as e:
        _report_config_error(e, environ, now, out)
        return 1

    tz = config.tz
    checked_at = floor_to_interval(now.astimezone(tz), config.check_interval_minutes)
    ctx = RunContext(run_id=_run_id(environ), checked_at=checked_at, env=config.env, tz=tz)

    repo = repo_factory(config)
    try:
        ok = CheckRunner(config, repo, Reporter(ctx, out), checked_at).run_all()
    finally:
        close = getattr(repo, "close", None)
        if close:
            close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run())
