"""统一的项目配置加载、运行时更新与平台路径。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "app.json"

EDITABLE_CONFIG_KEYS = (
    "app_name",
    "log_retention_days",
    "task_workers",
    "scheduler_poll_seconds",
    "sync_history_retention_days",
    "sync_history_max_runs_per_mapping",
)
RESTART_REQUIRED_CONFIG_KEYS = ("task_workers",)


@dataclass
class AppConfig:
    app_name: str
    host: str
    port: int
    timezone: str
    database_path: Path
    secret_file: Path
    log_file: Path
    log_retention_days: int
    task_workers: int
    scheduler_poll_seconds: int
    sync_history_retention_days: int
    sync_history_max_runs_per_mapping: int
    config_path: Path
    legacy_password_file: Path
    raw: dict[str, Any]

    def public_dict(self) -> dict[str, Any]:
        return {
            "app_name": self.app_name,
            "host": self.host,
            "port": self.port,
            "timezone": self.timezone,
            "database_path": str(self.database_path),
            "secret_file": str(self.secret_file),
            "log_file": str(self.log_file),
            "log_retention_days": self.log_retention_days,
            "task_workers": self.task_workers,
            "scheduler_poll_seconds": self.scheduler_poll_seconds,
            "sync_history_retention_days": self.sync_history_retention_days,
            "sync_history_max_runs_per_mapping": self.sync_history_max_runs_per_mapping,
        }

    def settings_payload(self) -> dict[str, Any]:
        return {
            "editable": {
                "app_name": self.app_name,
                "log_retention_days": self.log_retention_days,
                "task_workers": self.task_workers,
                "scheduler_poll_seconds": self.scheduler_poll_seconds,
                "sync_history_retention_days": self.sync_history_retention_days,
                "sync_history_max_runs_per_mapping": self.sync_history_max_runs_per_mapping,
            },
            "readonly": {
                "host": self.host,
                "port": self.port,
                "timezone": self.timezone,
                "database_path": str(self.database_path),
                "log_file": str(self.log_file),
            },
            "restart_required": list(RESTART_REQUIRED_CONFIG_KEYS),
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

    legacy_value = raw.get("password_file")
    legacy_password_file = (
        resolve(str(legacy_value))
        if legacy_value
        else (root / "data" / "password.txt").resolve()
    )
    raw.pop("password_file", None)

    return AppConfig(
        app_name=str(raw.get("app_name", "多网盘分享链接管理工具")),
        host=str(raw.get("host", "127.0.0.1")),
        port=int(raw.get("port", 8000)),
        timezone=str(raw.get("timezone", "Asia/Shanghai")),
        database_path=resolve(str(raw.get("database_path", "data/app.sqlite3"))),
        secret_file=resolve(str(raw.get("secret_file", "data/secrets.json"))),
        log_file=resolve(str(raw.get("log_file", "logs/app.log"))),
        log_retention_days=int(raw.get("log_retention_days", 30)),
        task_workers=max(1, int(raw.get("task_workers", 2))),
        scheduler_poll_seconds=max(5, int(raw.get("scheduler_poll_seconds", 15))),
        sync_history_retention_days=max(
            1, int(raw.get("sync_history_retention_days", 90))
        ),
        sync_history_max_runs_per_mapping=max(
            10, int(raw.get("sync_history_max_runs_per_mapping", 200))
        ),
        config_path=config_path,
        legacy_password_file=legacy_password_file,
        raw=raw,
    )


def save_app_config(cfg: AppConfig, updates: dict[str, Any]) -> None:
    """原子写回配置文件，并同步更新内存中的配置对象。"""
    raw = dict(cfg.raw)
    raw.update(updates)
    cfg.config_path.parent.mkdir(parents=True, exist_ok=True)
    temp = cfg.config_path.with_name(cfg.config_path.name + ".tmp")
    temp.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, cfg.config_path)
    cfg.raw = raw
    for key, value in updates.items():
        setattr(cfg, key, value)
