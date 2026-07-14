"""统一加载项目配置并解析跨平台路径。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "app.json"


@dataclass(frozen=True)
class AppConfig:
    app_name: str
    host: str
    port: int
    timezone: str
    database_path: Path
    password_file: Path
    secret_file: Path
    log_file: Path
    log_retention_days: int
    task_workers: int
    scheduler_poll_seconds: int
    raw: dict[str, Any]

    def public_dict(self) -> dict[str, Any]:
        return {
            "app_name": self.app_name,
            "host": self.host,
            "port": self.port,
            "timezone": self.timezone,
            "database_path": str(self.database_path),
            "password_file": str(self.password_file),
            "log_file": str(self.log_file),
            "log_retention_days": self.log_retention_days,
            "task_workers": self.task_workers,
            "scheduler_poll_seconds": self.scheduler_poll_seconds,
        }


def load_app_config(path: str | Path | None = None) -> AppConfig:
    config_path = Path(
        path or os.environ.get("BDPAN_CONFIG", DEFAULT_CONFIG_PATH)
    ).expanduser().resolve()
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    root = config_path.parent.parent

    def resolve(value: str) -> Path:
        candidate = Path(os.path.expanduser(value))
        return candidate if candidate.is_absolute() else (root / candidate).resolve()

    return AppConfig(
        app_name=str(raw.get("app_name", "百度网盘分享链接管理工具")),
        host=str(raw.get("host", "127.0.0.1")),
        port=int(raw.get("port", 8000)),
        timezone=str(raw.get("timezone", "Asia/Shanghai")),
        database_path=resolve(str(raw.get("database_path", "data/app.sqlite3"))),
        password_file=resolve(str(raw.get("password_file", "data/password.txt"))),
        secret_file=resolve(str(raw.get("secret_file", "data/secrets.json"))),
        log_file=resolve(str(raw.get("log_file", "logs/app.log"))),
        log_retention_days=int(raw.get("log_retention_days", 30)),
        task_workers=max(1, int(raw.get("task_workers", 2))),
        scheduler_poll_seconds=max(5, int(raw.get("scheduler_poll_seconds", 15))),
        raw=raw,
    )
