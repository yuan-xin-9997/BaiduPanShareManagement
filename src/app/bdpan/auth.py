"""用户密码哈希、默认管理员初始化和页面权限。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
from pathlib import Path

from .models import User

PAGES = ("shares", "mappings", "tasks", "settings", "users")
DEFAULT_USER_PAGES = ("shares", "mappings", "tasks")
DEFAULT_ADMIN_USERNAME = os.environ.get("BDPAN_ADMIN_USERNAME", "admin")
DEFAULT_ADMIN_PASSWORD = os.environ.get("BDPAN_ADMIN_PASSWORD", "admin123")

logger = logging.getLogger(__name__)


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32
    )
    return (
        f"{base64.urlsafe_b64encode(salt).decode()}:"
        f"{base64.urlsafe_b64encode(digest).decode()}"
    )


def password_valid(password: str, stored: str) -> bool:
    try:
        salt_text, _ = stored.split(":", 1)
        salt = base64.urlsafe_b64decode(salt_text)
        return hmac.compare_digest(password_hash(password, salt), stored)
    except (ValueError, TypeError):
        return False


def _save_admin(db, password_digest: str) -> int:
    user_id = db.upsert_user(
        DEFAULT_ADMIN_USERNAME, password_digest, "admin"
    )
    db.set_user_permissions(user_id, list(PAGES))
    return user_id


def ensure_admin_account(db) -> None:
    """保证系统始终至少有一个可用的管理员账号。"""
    users = db.list_users()
    if any(user.role == "admin" and user.active for user in users):
        return
    existing = db.get_user_by_username(DEFAULT_ADMIN_USERNAME)
    if existing:
        _save_admin(db, existing.password_hash)
        logger.warning(
            "系统没有可用的管理员账号，已将 %s 提升为管理员", DEFAULT_ADMIN_USERNAME
        )
        return
    _save_admin(db, password_hash(DEFAULT_ADMIN_PASSWORD))
    logger.warning(
        "系统没有可用的管理员账号，已创建默认管理员 %s，请使用默认凭据登录后立即修改密码",
        DEFAULT_ADMIN_USERNAME,
    )


def _read_legacy_records(path: Path) -> list[tuple[str, str, str]] | None:
    records: list[tuple[str, str, str]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        logger.exception("读取旧版账号文件失败: %s", path)
        return None
    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":")
        if len(parts) != 3:
            logger.warning("旧版账号文件第 %s 行格式错误，已跳过", number)
            continue
        username, password, role = (part.strip() for part in parts)
        if not username or not password or role not in {"admin", "user"}:
            logger.warning("旧版账号文件第 %s 行内容无效，已跳过", number)
            continue
        records.append((username, password, role))
    return records


def import_legacy_password_file(db, path: Path) -> int:
    """一次性导入旧版 password.txt，成功导入后删除明文文件。

    数据库是唯一事实来源。已存在的用户名不会被旧文件覆盖，只补充数据库中
    尚未出现的用户，避免升级后丢失旧文件里新增的账号。
    """
    if not path.exists():
        return 0
    records = _read_legacy_records(path)
    if records is None:
        return 0
    existing = {user.username for user in db.list_users()}
    imported = 0
    for username, password, role in records:
        if username in existing:
            continue
        user_id = db.upsert_user(username, password_hash(password), role)
        db.set_user_permissions(
            user_id,
            list(PAGES) if role == "admin" else list(DEFAULT_USER_PAGES),
        )
        existing.add(username)
        imported += 1
    try:
        path.unlink()
    except OSError:
        logger.exception("删除旧版账号文件失败: %s", path)
    else:
        logger.info(
            "已处理旧版账号文件 %s：导入 %s 个用户，文件已删除",
            path,
            imported,
        )
    return imported


def user_payload(user: User, pages: list[str]) -> dict[str, object]:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "pages": list(PAGES) if user.role == "admin" else pages,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
    }
