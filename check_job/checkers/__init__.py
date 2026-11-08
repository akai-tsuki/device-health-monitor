"""チェック種類ごとの純粋ロジック。実行順は main.py を参照。"""
from check_job.checkers import delay, lost, low_battery, missing

# 実行順。lost / low_battery は missing の結果を除外に使う（docs/design.md 6.5節）
CHECK_ORDER = (missing.CHECK_TYPE, delay.CHECK_TYPE, lost.CHECK_TYPE, low_battery.CHECK_TYPE)

# 異常時の status（docs/log-spec.md 2章）
ANOMALY_STATUS = {
    missing.CHECK_TYPE: missing.STATUS,
    delay.CHECK_TYPE: delay.STATUS,
    lost.CHECK_TYPE: lost.STATUS,
    low_battery.CHECK_TYPE: low_battery.STATUS,
}
