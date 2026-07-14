"""password.txt 用户同步、密码哈希和页面权限。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

from .models import User

PAGES = ("shares", "mappings", "tasks", "settings", "users")
DEFAULT_USER_PAGES = ("shares", "mappings", "tasks")


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


def read_password_file(path: Path) -> list[tuple[str, str, str]]:
    records: list[tuple[str, str, str]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return records
    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":")
        if len(parts) != 3:
            raise ValueError(f"password.txt 第 {number} 行格式错误")
        username, password, role = (part.strip() for part in parts)
        if not username or not password or role not in {"admin", "user"}:
            raise ValueError(f"password.txt 第 {number} 行内容无效")
        records.append((username, password, role))
    return records


def sync_password_file(db, path: Path) -> int:
    records = read_password_file(path)
    if not records:
        raise ValueError("password.txt 中没有可登录用户")
    db.deactivate_users_except([username for username, _, _ in records])
    for username, password, role in records:
        existing = db.get_user_by_username(username)
        digest = (
            existing.password_hash
            if existing and password_valid(password, existing.password_hash)
            else password_hash(password)
        )
        user_id = db.upsert_user(username, digest, role)
        if role == "admin":
            db.set_user_permissions(user_id, list(PAGES))
        elif existing is None:
            db.set_user_permissions(user_id, list(DEFAULT_USER_PAGES))
    return len(records)


def write_password_file(path: Path, users: list[tuple[str, str, str]]) -> None:
    header = (
        "# 格式: username:password:role  (role 取值: admin | user)\n"
        "# admin 默认拥有所有页面权限；user 的可见页面由管理员在权限管理页配置。\n"
        "# 修改本文件后，新用户在下次登录时会自动同步到数据库。\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        header + "".join(f"{u}:{p}:{r}\n" for u, p, r in users),
        encoding="utf-8",
    )
    os.replace(temp, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def user_payload(user: User, pages: list[str]) -> dict[str, object]:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "pages": list(PAGES) if user.role == "admin" else pages,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
    }
