"""チェック共通の型とサマリ集計。docs/design.md 6.5節、docs/log-spec.md 6章を参照。

checkers/ 配下は DB・GCP に依存しない純粋なロジックとし、repository.py が取得したデータと
パラメータ・checked_at だけから CheckOutcome を組み立てる。
"""
from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class ActiveUser:
    """users.status = 'active' のユーザー。"""

    user_id: str
    group_id: str
    user_type: str


@dataclass(frozen=True)
class Detail:
    """該当ユーザー1人分の詳細。fields はチェック種類ごとの追加フィールド（log-spec.md 7.2〜7.5）。

    fields の値は datetime / int / float / None。ISO 8601 への変換は reporter.py が行う。
    """

    user: ActiveUser
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Summary:
    group_id: str
    user_type: str
    monitored_user_count: int
    anomaly_user_count: int


@dataclass(frozen=True)
class Totals:
    group_count: int
    monitored_user_count: int
    anomaly_user_count: int


@dataclass(frozen=True)
class CheckOutcome:
    details: list[Detail]
    summaries: list[Summary]
    totals: Totals

    @property
    def anomaly_user_ids(self) -> frozenset[str]:
        return frozenset(d.user.user_id for d in self.details)


def build_outcome(
    users: Iterable[ActiveUser], details: list[Detail], *, include_normal_summaries: bool = True
) -> CheckOutcome:
    """全アクティブユーザーと該当ユーザーの詳細から、サマリ・合計を組み立てる。

    - include_normal_summaries=True（missing / lost）：サマリはアクティブユーザーが1人以上いる
      グループ×ユーザタイプ ごとに必ず1件（該当0でも出す）
    - include_normal_summaries=False（delay / low_battery）：該当1人以上のグループ×ユーザタイプだけ出す
      （log-spec.md 6章）。totals は常に全ユーザーで集計する
    - monitored_user_count は判定対象外（除外）のユーザーも含めたアクティブユーザー数（6.1節）
    """
    users = list(users)
    anomaly_ids = {d.user.user_id for d in details}

    monitored: dict[tuple[str, str], int] = {}
    anomaly: dict[tuple[str, str], int] = {}
    for u in users:
        key = (u.group_id, u.user_type)
        monitored[key] = monitored.get(key, 0) + 1
        anomaly.setdefault(key, 0)
        if u.user_id in anomaly_ids:
            anomaly[key] += 1

    summaries = [
        Summary(group_id=g, user_type=t, monitored_user_count=monitored[(g, t)],
                anomaly_user_count=anomaly[(g, t)])
        for g, t in sorted(monitored)
        if include_normal_summaries or anomaly[(g, t)] > 0
    ]
    totals = Totals(
        group_count=len({u.group_id for u in users}),
        monitored_user_count=len(users),
        anomaly_user_count=sum(anomaly.values()),
    )
    sorted_details = sorted(details, key=lambda d: (d.user.group_id, d.user.user_type, d.user.user_id))
    return CheckOutcome(details=sorted_details, summaries=summaries, totals=totals)
