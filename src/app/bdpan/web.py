"""FastAPI Web 服务、认证与页面级授权。"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import shutil
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .auth import (
    PAGES, password_hash, password_valid, read_password_file,
    sync_password_file, user_payload, write_password_file,
)
from .client import BaiduPanClient
from .config import AppConfig, load_app_config
from .database import Database
from .models import FileEntry, ShareLink, SyncMapping, User
from .storage import probe_storage, validate_storage_path
from .web_tasks import TaskManager

logger = logging.getLogger("bdpan.web")
PACKAGE_DIR = Path(__file__).parent
LEGACY_DB = Path.home() / ".local" / "share" / "bdpan" / "bdpan.db"
LEGACY_CONFIG = Path.home() / ".config" / "bdpan" / "config.json"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _ensure_password_file(path: Path) -> None:
    if path.exists():
        return
    write_password_file(path, [("admin", "admin123", "admin")])


def _prepare_database(cfg: AppConfig, allow_legacy_migration: bool) -> None:
    cfg.database_path.parent.mkdir(parents=True, exist_ok=True)
    if allow_legacy_migration and not cfg.database_path.exists() and LEGACY_DB.exists():
        shutil.copy2(LEGACY_DB, cfg.database_path)


def _configure_logging(cfg: AppConfig) -> None:
    cfg.log_file.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if any(getattr(handler, "_bdpan_handler", False) for handler in root.handlers):
        return
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    timezone = ZoneInfo(cfg.timezone)
    formatter.converter = lambda stamp: __import__("datetime").datetime.fromtimestamp(
        stamp, timezone
    ).timetuple()
    handler = TimedRotatingFileHandler(
        cfg.log_file, when="midnight", backupCount=cfg.log_retention_days,
        encoding="utf-8",
    )
    handler._bdpan_handler = True  # type: ignore[attr-defined]
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def create_app(
    data_dir: str | Path | None = None,
    config_path: str | Path | None = None,
) -> FastAPI:
    cfg = load_app_config(config_path)
    testing_override = data_dir is not None
    if data_dir is not None:
        root = Path(data_dir).expanduser().resolve()
        cfg = AppConfig(
            app_name=cfg.app_name, host=cfg.host, port=cfg.port,
            timezone=cfg.timezone, database_path=root / "app.sqlite3",
            password_file=root / "password.txt", secret_file=root / "secrets.json",
            log_file=root / "logs" / "app.log",
            log_retention_days=cfg.log_retention_days,
            task_workers=cfg.task_workers,
            scheduler_poll_seconds=cfg.scheduler_poll_seconds, raw=cfg.raw,
        )
    _prepare_database(cfg, allow_legacy_migration=not testing_override)
    _ensure_password_file(cfg.password_file)
    _configure_logging(cfg)

    secrets_config = _read_json(cfg.secret_file)
    if not secrets_config.get("session_secret"):
        secrets_config["session_secret"] = secrets.token_urlsafe(48)
        legacy_cookie = _read_json(LEGACY_CONFIG).get("cookie", "")
        if legacy_cookie:
            secrets_config["cookie"] = legacy_cookie
        _write_json(cfg.secret_file, secrets_config)

    def secret_values() -> dict[str, Any]:
        return _read_json(cfg.secret_file)

    def cookie() -> str:
        value = secret_values().get("cookie", "") or os.environ.get("BDPAN_COOKIE", "")
        if not value:
            raise ValueError("尚未配置百度网盘 Cookie")
        return str(value)

    def db() -> Database:
        return Database(str(cfg.database_path))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = db()
        try:
            sync_password_file(store, cfg.password_file)
        finally:
            store.close()
        app.state.tasks = TaskManager(
            str(cfg.database_path), cookie, max_workers=cfg.task_workers,
            scheduler_poll_seconds=cfg.scheduler_poll_seconds,
        )
        yield
        app.state.tasks.close()

    app = FastAPI(title=cfg.app_name, lifespan=lifespan)
    app.state.config = cfg
    app.state.db_path = str(cfg.database_path)
    app.mount("/assets", StaticFiles(directory=PACKAGE_DIR / "web_static" / "assets"), name="assets")

    def make_token(username: str) -> str:
        expiry = int(time.time()) + 86400 * 7
        payload = f"{username}:{expiry}"
        signature = hmac.new(
            secret_values()["session_secret"].encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        return base64.urlsafe_b64encode(f"{payload}:{signature}".encode()).decode()

    def current_user(request: Request) -> User:
        token = request.cookies.get("bdpan_session", "")
        try:
            raw = base64.urlsafe_b64decode(token).decode()
            username, expiry, signature = raw.rsplit(":", 2)
            expected = hmac.new(
                secret_values()["session_secret"].encode(),
                f"{username}:{expiry}".encode(), hashlib.sha256,
            ).hexdigest()
            if int(expiry) < time.time() or not hmac.compare_digest(signature, expected):
                raise ValueError
        except (KeyError, ValueError, TypeError):
            raise HTTPException(401, "请先登录")
        store = db()
        try:
            user = store.get_user_by_username(username)
        finally:
            store.close()
        if not user or not user.active:
            raise HTTPException(401, "账号不存在或已停用")
        return user

    def require_page(page: str) -> Callable[[User], User]:
        def dependency(user: User = Depends(current_user)) -> User:
            if user.role == "admin":
                return user
            store = db()
            try:
                allowed = page in store.get_user_permissions(user.id or 0)
            finally:
                store.close()
            if not allowed:
                raise HTTPException(403, "无权访问此页面或执行此操作")
            return user
        return dependency

    def require_admin(user: User = Depends(current_user)) -> User:
        if user.role != "admin":
            raise HTTPException(403, "仅管理员可执行此操作")
        return user

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(PACKAGE_DIR / "web_static" / "index.html")

    @app.get("/api/bootstrap")
    def bootstrap(request: Request) -> dict[str, Any]:
        try:
            user = current_user(request)
        except HTTPException:
            return {"authenticated": False, "app_name": cfg.app_name}
        store = db()
        try:
            pages = store.get_user_permissions(user.id or 0)
        finally:
            store.close()
        return {
            "authenticated": True, "app_name": cfg.app_name,
            "user": user_payload(user, pages),
        }

    @app.post("/api/login")
    async def login(request: Request, response: Response) -> dict[str, Any]:
        body = await request.json()
        store = db()
        try:
            sync_password_file(store, cfg.password_file)
            user = store.get_user_by_username(str(body.get("username", "")))
            if not user or not password_valid(str(body.get("password", "")), user.password_hash):
                raise HTTPException(401, "用户名或密码错误")
            pages = store.get_user_permissions(user.id or 0)
        finally:
            store.close()
        response.set_cookie(
            "bdpan_session", make_token(user.username), httponly=True,
            samesite="lax", max_age=86400 * 7,
        )
        return {"ok": True, "user": user_payload(user, pages)}

    @app.post("/api/logout")
    def logout(response: Response) -> dict[str, bool]:
        response.delete_cookie("bdpan_session")
        return {"ok": True}

    @app.get("/api/state")
    def state(user: User = Depends(current_user)) -> dict[str, Any]:
        store = db()
        try:
            pages = list(PAGES) if user.role == "admin" else store.get_user_permissions(user.id or 0)
            links: list[dict[str, Any]] = []
            if "shares" in pages or "mappings" in pages:
                for link in store.list_share_links():
                    item = asdict(link)
                    item["file_count"] = len(store.get_file_entries(link.id or 0))
                    item.pop("password", None)
                    links.append(item)
            return {
                "user": user_payload(user, pages),
                "links": links,
                "mappings": [asdict(x) for x in store.get_all_sync_mappings()]
                if "mappings" in pages else [],
                "tasks": app.state.tasks.list_tasks() if "tasks" in pages else [],
                "runs": store.list_sync_runs() if "tasks" in pages else [],
                "cookie_configured": bool(cookie()) if (
                    secret_values().get("cookie") or os.environ.get("BDPAN_COOKIE")
                ) else False,
            }
        finally:
            store.close()

    @app.get("/api/settings")
    def get_settings(_: User = Depends(require_page("settings"))) -> dict[str, Any]:
        return {
            "config": cfg.public_dict(),
            "cookie_configured": bool(
                secret_values().get("cookie") or os.environ.get("BDPAN_COOKIE")
            ),
        }

    @app.put("/api/settings")
    async def update_settings(request: Request, _: User = Depends(require_page("settings"))) -> dict[str, bool]:
        body = await request.json()
        values = secret_values()
        if str(body.get("cookie", "")).strip():
            values["cookie"] = str(body["cookie"]).strip()
        _write_json(cfg.secret_file, values)
        return {"ok": True}

    @app.get("/api/users")
    def list_users(_: User = Depends(require_admin)) -> list[dict[str, Any]]:
        store = db()
        try:
            return [
                user_payload(item, store.get_user_permissions(item.id or 0))
                for item in store.list_users()
            ]
        finally:
            store.close()

    @app.post("/api/users")
    async def add_user(request: Request, _: User = Depends(require_admin)) -> dict[str, int]:
        body = await request.json()
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))
        role = str(body.get("role", "user"))
        pages = _valid_pages(body.get("pages", []))
        if (not username or ":" in username or ":" in password
                or len(password) < 8 or role not in {"admin", "user"}):
            raise HTTPException(400, "用户名、密码或角色无效；密码至少 8 个字符")
        records = read_password_file(cfg.password_file)
        if any(item[0] == username for item in records):
            raise HTTPException(409, "用户名已存在")
        records.append((username, password, role))
        write_password_file(cfg.password_file, records)
        store = db()
        try:
            user_id = store.upsert_user(username, password_hash(password), role)
            store.set_user_permissions(user_id, list(PAGES) if role == "admin" else pages)
            return {"id": user_id}
        finally:
            store.close()

    @app.put("/api/users/{user_id}")
    async def edit_user(user_id: int, request: Request, actor: User = Depends(require_admin)) -> dict[str, bool]:
        body = await request.json()
        store = db()
        try:
            target = store.get_user(user_id)
            if not target:
                raise HTTPException(404, "用户不存在")
            role = str(body.get("role", target.role))
            if actor.id == user_id and role != "admin":
                raise HTTPException(400, "不能取消当前登录管理员的管理员角色")
            if role not in {"admin", "user"}:
                raise HTTPException(400, "角色无效")
            password = str(body.get("password", ""))
            if password and (len(password) < 8 or ":" in password):
                raise HTTPException(400, "密码至少 8 个字符")
            records = read_password_file(cfg.password_file)
            updated: list[tuple[str, str, str]] = []
            plaintext = ""
            for username, old_password, old_role in records:
                if username == target.username:
                    plaintext = password or old_password
                    updated.append((username, plaintext, role))
                else:
                    updated.append((username, old_password, old_role))
            if not plaintext:
                raise HTTPException(400, "password.txt 中未找到该用户")
            write_password_file(cfg.password_file, updated)
            store.upsert_user(target.username, password_hash(plaintext), role)
            store.set_user_permissions(
                user_id, list(PAGES) if role == "admin" else _valid_pages(body.get("pages", []))
            )
            return {"ok": True}
        finally:
            store.close()

    @app.delete("/api/users/{user_id}")
    def delete_user(user_id: int, actor: User = Depends(require_admin)) -> dict[str, bool]:
        if actor.id == user_id:
            raise HTTPException(400, "不能删除当前登录账号")
        store = db()
        try:
            target = store.get_user(user_id)
            if not target:
                raise HTTPException(404, "用户不存在")
            records = [item for item in read_password_file(cfg.password_file) if item[0] != target.username]
            if not any(item[2] == "admin" for item in records):
                raise HTTPException(400, "系统必须至少保留一个管理员")
            write_password_file(cfg.password_file, records)
            store.delete_user(user_id)
            return {"ok": True}
        finally:
            store.close()

    @app.post("/api/shares")
    async def add_share(request: Request, _: User = Depends(require_page("shares"))) -> dict[str, str]:
        body = await request.json()
        url, password, note = body.get("url", ""), body.get("password", ""), body.get("note", "")
        if not url:
            raise HTTPException(400, "分享链接不能为空")

        def work(progress):
            progress("正在读取百度网盘目录")
            client = BaiduPanClient(cookie=cookie())
            try:
                info = client.parse_share(url, password)
            finally:
                client.close()
            store = db()
            try:
                link_id = store.add_share_link(ShareLink(
                    None, url, info.surl, password, info.title, info.share_id,
                    info.share_uk, "active", time.time(), time.time(), note,
                ))
                store.add_file_entries([_entry(link_id, item) for item in info.files])
            finally:
                store.close()
            return f"已添加，共索引 {len(info.files)} 个条目"
        return {"task_id": app.state.tasks.submit("add", "添加分享链接", work)}

    @app.put("/api/shares/{link_id}")
    async def edit_share(link_id: int, request: Request, _: User = Depends(require_page("shares"))) -> dict[str, bool]:
        body = await request.json()
        store = db()
        try:
            link = store.get_share_link(link_id)
            if not link:
                raise HTTPException(404, "分享链接不存在")
            for field in ("url", "password", "note"):
                if field in body:
                    setattr(link, field, str(body[field]))
            store.update_share_link(link)
            return {"ok": True}
        finally:
            store.close()

    @app.delete("/api/shares/{link_id}")
    def delete_share(link_id: int, _: User = Depends(require_page("shares"))) -> dict[str, bool]:
        store = db()
        try:
            store.delete_share_link(link_id)
            return {"ok": True}
        finally:
            store.close()

    @app.post("/api/shares/{link_id}/refresh")
    def refresh_share(link_id: int, _: User = Depends(require_page("shares"))) -> dict[str, str]:
        def work(progress):
            store = db()
            link = store.get_share_link(link_id)
            store.close()
            if not link:
                raise ValueError("分享链接不存在")
            progress("正在重新读取远端目录")
            client = BaiduPanClient(cookie=cookie())
            try:
                info = client.parse_share(link.url, link.password)
            finally:
                client.close()
            store = db()
            try:
                store.clear_file_entries(link_id)
                store.add_file_entries([_entry(link_id, item) for item in info.files])
                link.title, link.status, link.last_checked = info.title, "active", time.time()
                store.update_share_link(link)
            finally:
                store.close()
            return f"刷新完成，共 {len(info.files)} 个条目"
        return {"task_id": app.state.tasks.submit("refresh", f"刷新分享 #{link_id}", work)}

    @app.get("/api/shares/{link_id}/entries")
    def entries(link_id: int, parent: str = "", _: User = Depends(require_page("shares"))) -> list[dict[str, Any]]:
        store = db()
        try:
            all_entries = store.get_file_entries(link_id)
        finally:
            store.close()
        parent = parent.rstrip("/")
        result = [
            asdict(entry) for entry in all_entries
            if entry.path.rsplit("/", 1)[0] == parent
        ]
        return sorted(result, key=lambda item: (not item["is_dir"], item["name"].lower()))

    @app.post("/api/mappings")
    async def add_mapping(request: Request, _: User = Depends(require_page("mappings"))) -> dict[str, int]:
        mapping = _mapping_from_body(await request.json())
        store = db()
        try:
            if not store.get_share_link(mapping.share_link_id):
                raise HTTPException(400, "分享链接不存在")
            return {"id": store.add_sync_mapping(mapping)}
        finally:
            store.close()

    @app.post("/api/storage/probe")
    async def storage_probe(request: Request, _: User = Depends(require_page("mappings"))) -> dict[str, object]:
        body = await request.json()
        return probe_storage(str(body.get("local_path", "")), str(body.get("storage_type", "local")))

    @app.put("/api/mappings/{mapping_id}")
    async def edit_mapping(mapping_id: int, request: Request, _: User = Depends(require_page("mappings"))) -> dict[str, bool]:
        mapping = _mapping_from_body(await request.json(), mapping_id)
        store = db()
        try:
            if not store.get_sync_mapping(mapping_id):
                raise HTTPException(404, "同步映射不存在")
            store.update_sync_mapping(mapping)
            return {"ok": True}
        finally:
            store.close()

    @app.delete("/api/mappings/{mapping_id}")
    def delete_mapping(mapping_id: int, _: User = Depends(require_page("mappings"))) -> dict[str, bool]:
        store = db()
        try:
            store.delete_sync_mapping(mapping_id)
            return {"ok": True}
        finally:
            store.close()

    @app.post("/api/mappings/{mapping_id}/sync")
    def sync_mapping(mapping_id: int, _: User = Depends(require_page("mappings"))) -> dict[str, str]:
        task_id = app.state.tasks.submit_sync(mapping_id)
        if task_id is None:
            raise HTTPException(409, "该映射正在同步")
        return {"task_id": task_id}

    @app.post("/api/sync-all")
    def sync_all(_: User = Depends(require_page("mappings"))) -> dict[str, list[str]]:
        store = db()
        try:
            ids = [item.id for item in store.get_all_sync_mappings() if item.id is not None]
        finally:
            store.close()
        return {"task_ids": [task for mid in ids if (task := app.state.tasks.submit_sync(mid))]}

    @app.exception_handler(ValueError)
    async def value_error_handler(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return app


def _valid_pages(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise HTTPException(400, "页面权限格式无效")
    return [page for page in value if page in PAGES and page != "users"]


def _entry(link_id: int, item: Any) -> FileEntry:
    return FileEntry(None, link_id, item.fs_id, item.name, item.path, item.is_dir,
                     item.size, item.md5, item.modified_time, None)


def _mapping_from_body(body: dict[str, Any], mapping_id: int | None = None) -> SyncMapping:
    local_path = str(body.get("local_path", "")).strip()
    remote_path = str(body.get("remote_path", "")).strip()
    if not local_path or not remote_path:
        raise HTTPException(400, "远端路径和目标路径不能为空")
    storage_type = str(body.get("storage_type", "local"))
    try:
        local = validate_storage_path(local_path, storage_type)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    interval = int(body.get("schedule_interval", 60))
    if interval < 1:
        raise HTTPException(400, "自动同步间隔至少为 1 分钟")
    strategy = str(body.get("sync_strategy", "copy_new"))
    if strategy not in {"copy_new", "mirror", "ask"}:
        raise HTTPException(400, "同步策略无效")
    return SyncMapping(
        mapping_id, int(body["share_link_id"]), remote_path, local,
        bool(body.get("auto_sync", False)), float(body.get("last_synced", 0)),
        strategy, interval, storage_type,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="百度网盘分享管理 Web 服务")
    parser.add_argument("--config", default=os.environ.get("BDPAN_CONFIG"))
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    if args.config:
        os.environ["BDPAN_CONFIG"] = str(Path(args.config).resolve())
    cfg = load_app_config(args.config)
    import uvicorn
    uvicorn.run(
        "bdpan.web:create_app", factory=True,
        host=args.host or cfg.host, port=args.port or cfg.port,
    )


if __name__ == "__main__":
    main()
