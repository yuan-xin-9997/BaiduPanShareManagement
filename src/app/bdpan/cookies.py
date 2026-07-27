"""多 Cookie 的秘密存储、迁移、校验辅助与脱敏。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .database import Database
from .models import CookieRecord

ENV_COOKIE_ID = 0
ENV_COOKIE_NAME = "环境变量 Cookie"
SENSITIVE_PATTERN = re.compile(
    r"(?i)(BDUSS|STOKEN|BDCLND|PANPSC|BAIDUID)\s*=\s*([^;\s]+)"
)


def validate_cookie_value(value: str) -> str:
    value = value.strip()
    if not value or "\n" in value or "\r" in value:
        raise ValueError("Cookie 格式无效")
    names = {
        part.split("=", 1)[0].strip().upper()
        for part in value.split(";") if "=" in part
    }
    if "BDUSS" not in names:
        raise ValueError("Cookie 必须包含 BDUSS")
    return value


def mask_cookie(value: str) -> str:
    names = [
        part.split("=", 1)[0].strip()
        for part in value.split(";") if "=" in part
    ]
    visible = ", ".join(dict.fromkeys(name for name in names if name))
    return f"包含 {visible or '未知字段'}（内容已隐藏）"


def redact_sensitive(value: object) -> str:
    text = str(value)
    return SENSITIVE_PATTERN.sub(lambda match: f"{match.group(1)}=***", text)


class CookieSecretStore:
    """将 Cookie 原文保存在 secrets.json 的 ``cookies`` 映射中。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temp, self.path)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def get(self, cookie_id: int) -> str:
        if cookie_id == ENV_COOKIE_ID:
            value = os.environ.get("BDPAN_COOKIE", "")
        else:
            value = self._read().get("cookies", {}).get(str(cookie_id), "")
        if not value:
            raise ValueError("关联的 Cookie 不存在或秘密已丢失")
        return str(value)

    def set(self, cookie_id: int, value: str) -> None:
        data = self._read()
        cookies = data.setdefault("cookies", {})
        if not isinstance(cookies, dict):
            cookies = data["cookies"] = {}
        cookies[str(cookie_id)] = validate_cookie_value(value)
        self._write(data)

    def delete(self, cookie_id: int) -> None:
        data = self._read()
        cookies = data.get("cookies", {})
        if isinstance(cookies, dict):
            cookies.pop(str(cookie_id), None)
        self._write(data)

    def migrate_legacy(self, db: Database) -> int | None:
        data = self._read()
        legacy = str(data.get("cookie", "")).strip()
        existing = db.get_cookie_by_name("默认 Cookie")
        cookie_id = existing.id if existing else None
        if legacy and cookie_id is None:
            cookie_id = db.add_cookie("默认 Cookie", mask_cookie(legacy))
        if legacy and cookie_id is not None:
            cookies = data.setdefault("cookies", {})
            if isinstance(cookies, dict):
                cookies.setdefault(str(cookie_id), legacy)
            data.pop("cookie", None)
            self._write(data)
        if cookie_id is not None:
            db.associate_unassigned_links(cookie_id)
        return cookie_id

    def list_orphan_ids(self, db: Database) -> list[int]:
        known = {item.id for item in db.list_cookies()}
        cookies = self._read().get("cookies", {})
        if not isinstance(cookies, dict):
            return []
        return sorted(
            int(key) for key in cookies
            if key.isdigit() and int(key) not in known
        )


def environment_cookie_record() -> CookieRecord | None:
    value = os.environ.get("BDPAN_COOKIE", "")
    if not value:
        return None
    return CookieRecord(
        id=ENV_COOKIE_ID, name=ENV_COOKIE_NAME, masked_value=mask_cookie(value),
        status="unknown", created_at=0, updated_at=0, readonly=True,
    )
