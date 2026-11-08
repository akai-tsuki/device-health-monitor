"""仮想デバイスの振る舞い（DBに依存しない純粋なロジック）。docs/design.md 7.1節。

役割（role）はユーザーIDに埋め込む：demo-user-g{グループ}-{役割}-{連番}
デバイスごとのランダムなパラメータ（電池の周期・位相、ロストの位相など）は user_id をシードに
決定的に生成するため、シミュレーターを再起動しても同じデバイスは同じ振る舞いを続ける。
"""
import random
import re
from dataclasses import dataclass
from typing import Optional

ROLES = ("normal", "delay", "lost", "missing", "never")
USER_ID_PREFIX = "demo-user-"
GROUP_ID_PREFIX = "demo-group-"
USER_TYPE = "default"

_USER_ID_RE = re.compile(r"^demo-user-g(\d+)-([a-z]+)-(\d+)$")

# 通常の登録遅延（秒）
NORMAL_DELAY_RANGE = (1.0, 2.0)
# delay 役割：遅延させる割合・遅延幅（秒）
DELAY_RATIO = 0.2
DELAYED_RANGE = (20.0, 60.0)
# lost 役割：LOST_CYCLE_SECONDS 周期で LOST_PAUSE_SECONDS 送信を止める。
# 停止開始をチェック間隔（5分）の境界に揃え、ずれは LOST_MAX_JITTER_SECONDS までに抑える。
# こうすると停止を含む5分の時間帯は「境界〜停止開始」＋「停止終了〜次の境界」の計60秒分
# （送信間隔10秒で約6件）しか登録されず、閾値8件以下として確実に lost になる。
# 停止は15分（missing 閾値）より十分短いため、missing にはならない。
LOST_CYCLE_SECONDS = 15 * 60
LOST_PAUSE_SECONDS = 4 * 60
LOST_ALIGN_SECONDS = 5 * 60
LOST_MAX_JITTER_SECONDS = 20
# バッテリー：10V → 0V へ一定速度で下がり、0Vに達したら10Vへ戻るノコギリ波
BATTERY_MAX_VOLTAGE = 10.0
BATTERY_PERIOD_RANGE_SECONDS = (30 * 60, 90 * 60)


@dataclass(frozen=True)
class Device:
    user_id: str
    group_id: str
    role: str
    battery_period_seconds: int
    battery_phase_seconds: int
    lost_offset_seconds: int  # lost 役割の停止開始（周期内の位置）

    @classmethod
    def create(cls, group_no: int, role: str, serial: int) -> "Device":
        if role not in ROLES:
            raise ValueError(f"unknown role: {role}")
        user_id = f"{USER_ID_PREFIX}g{group_no}-{role}-{serial:02d}"
        rng = random.Random(user_id)
        period = rng.randint(*BATTERY_PERIOD_RANGE_SECONDS)
        return cls(
            user_id=user_id,
            group_id=f"{GROUP_ID_PREFIX}{group_no}",
            role=role,
            battery_period_seconds=period,
            battery_phase_seconds=rng.randrange(period),
            lost_offset_seconds=(
                rng.randrange(LOST_CYCLE_SECONDS // LOST_ALIGN_SECONDS) * LOST_ALIGN_SECONDS
                + rng.randint(0, LOST_MAX_JITTER_SECONDS)
            ),
        )


def parse_role(user_id: str) -> Optional[str]:
    m = _USER_ID_RE.match(user_id)
    return m.group(2) if m else None


def build_devices(group_count: int, role_counts: dict[str, int]) -> list[Device]:
    devices = []
    for g in range(1, group_count + 1):
        for role in ROLES:
            for n in range(1, role_counts.get(role, 0) + 1):
                devices.append(Device.create(g, role, n))
    return devices


def parse_role_counts(spec: str) -> dict[str, int]:
    """"normal=3,delay=1" 形式を辞書にする。"""
    counts: dict[str, int] = {}
    for part in filter(None, (p.strip() for p in spec.split(","))):
        role, _, num = part.partition("=")
        role = role.strip()
        if role not in ROLES or not num.strip().isdigit():
            raise ValueError(f"invalid role count: {part!r} (roles: {', '.join(ROLES)})")
        counts[role] = int(num)
    return counts


def battery_voltage(device: Device, epoch_seconds: float) -> float:
    position = (epoch_seconds + device.battery_phase_seconds) % device.battery_period_seconds
    voltage = BATTERY_MAX_VOLTAGE * (1 - position / device.battery_period_seconds)
    return round(voltage, 2)


def is_lost_paused(device: Device, epoch_seconds: float) -> bool:
    position = (epoch_seconds - device.lost_offset_seconds) % LOST_CYCLE_SECONDS
    return position < LOST_PAUSE_SECONDS


def should_send(device: Device, epoch_seconds: float, has_any_record: bool) -> bool:
    if device.role in ("normal", "delay"):
        return True
    if device.role == "lost":
        return not is_lost_paused(device, epoch_seconds)
    if device.role == "missing":
        return not has_any_record  # 初回に1件だけ登録し、以降は送信しない
    return False  # never


def registration_delay_seconds(device: Device, rng: random.Random) -> float:
    """registered_at - processed_at。遅延レコードは processed_at を遡らせて表現する。"""
    if device.role == "delay" and rng.random() < DELAY_RATIO:
        return rng.uniform(*DELAYED_RANGE)
    return rng.uniform(*NORMAL_DELAY_RANGE)
