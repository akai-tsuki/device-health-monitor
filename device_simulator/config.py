"""環境変数からの設定読み込み。GCE上では /etc/device-simulator/env（起動スクリプトが生成）から渡される。

DBパスワードは DB_PASSWORD を直接指定するか、DB_PASSWORD_SECRET（Secret Manager のシークレット名）を
指定して起動時に取得する（GCEのメタデータサーバーからアクセストークンを得て REST API を呼ぶため、
追加のPythonライブラリは不要）。
"""
import base64
import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Mapping

from device_simulator.behavior import parse_role_counts

_METADATA = "http://metadata.google.internal/computeMetadata/v1"
_DEFAULT_ROLE_COUNTS = "normal=3,delay=1,lost=1,missing=1,never=1"


@dataclass(frozen=True)
class Config:
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    send_interval_seconds: int
    group_count: int
    role_counts: dict
    retention_hours: int
    purge_interval_seconds: int

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> "Config":
        password = environ.get("DB_PASSWORD") or ""
        if not password and environ.get("DB_PASSWORD_SECRET"):
            password = fetch_secret(environ["DB_PASSWORD_SECRET"])
        if not password:
            raise ValueError("DB_PASSWORD or DB_PASSWORD_SECRET is required")
        return cls(
            db_host=environ["DB_HOST"],
            db_port=int(environ.get("DB_PORT", "5432")),
            db_name=environ["DB_NAME"],
            db_user=environ["DB_USER"],
            db_password=password,
            send_interval_seconds=int(environ.get("SIM_SEND_INTERVAL_SECONDS", "10")),
            group_count=int(environ.get("SIM_GROUP_COUNT", "3")),
            role_counts=parse_role_counts(environ.get("SIM_ROLE_COUNTS", _DEFAULT_ROLE_COUNTS)),
            retention_hours=int(environ.get("SIM_RETENTION_HOURS", "24")),
            purge_interval_seconds=int(environ.get("SIM_PURGE_INTERVAL_SECONDS", "3600")),
        )


def _metadata_get(path: str) -> bytes:
    req = urllib.request.Request(f"{_METADATA}/{path}", headers={"Metadata-Flavor": "Google"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read()


def fetch_secret(secret_name: str) -> str:
    """Secret Manager の最新バージョンを取得する。secret_name はシークレットID（例 db-password-dev）。"""
    project_id = _metadata_get("project/project-id").decode()
    token = json.loads(_metadata_get("instance/service-accounts/default/token"))["access_token"]
    url = (
        f"https://secretmanager.googleapis.com/v1/projects/{project_id}"
        f"/secrets/{secret_name}/versions/latest:access"
    )
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        payload = json.loads(resp.read())["payload"]["data"]
    return base64.b64decode(payload).decode()
