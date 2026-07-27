import json
import os
import sqlite3
import time
from pathlib import Path

from fastapi.testclient import TestClient

from bdpan.cookies import CookieSecretStore, mask_cookie, redact_sensitive
from bdpan.database import Database
from bdpan.models import ShareLink
from bdpan.web import create_app


def _link(cookie_id: int | None = None) -> ShareLink:
    return ShareLink(
        None, "https://pan.baidu.com/s/1test", "test", "", "测试",
        1, 2, "active", time.time(), time.time(), "", cookie_id,
    )


def test_cookie_metadata_crud_and_reference_guard(tmp_path: Path) -> None:
    store = Database(str(tmp_path / "app.sqlite3"))
    cookie_id = store.add_cookie("主账号", mask_cookie("BDUSS=secret; STOKEN=x"))
    assert store.get_cookie(cookie_id).status == "unknown"
    assert store.get_cookie_by_name("主账号").id == cookie_id
    store.update_cookie_status(cookie_id, "valid")
    assert store.get_cookie(cookie_id).status == "valid"
    store.add_share_link(_link(cookie_id))
    assert store.cookie_reference_count(cookie_id) == 1
    try:
        store.delete_cookie(cookie_id)
    except ValueError as exc:
        assert "重新关联" in str(exc)
    else:
        raise AssertionError("被引用的 Cookie 不应允许删除")
    store.close()


def test_legacy_cookie_migration_is_idempotent(tmp_path: Path) -> None:
    secret_path = tmp_path / "secrets.json"
    secret_path.write_text(
        json.dumps({"session_secret": "x", "cookie": "BDUSS=legacy"}),
        encoding="utf-8",
    )
    store = Database(str(tmp_path / "app.sqlite3"))
    link_id = store.add_share_link(_link())
    secrets = CookieSecretStore(secret_path)
    first_id = secrets.migrate_legacy(store)
    second_id = secrets.migrate_legacy(store)
    assert first_id == second_id
    assert len(store.list_cookies()) == 1
    assert store.get_share_link(link_id).cookie_id == first_id
    payload = json.loads(secret_path.read_text(encoding="utf-8"))
    assert "cookie" not in payload
    assert payload["cookies"][str(first_id)] == "BDUSS=legacy"
    store.close()


def test_secret_store_is_masked_and_redacts_errors(tmp_path: Path) -> None:
    store = Database(str(tmp_path / "app.sqlite3"))
    cookie_id = store.add_cookie("账号", mask_cookie("BDUSS=secret"))
    secrets = CookieSecretStore(tmp_path / "secrets.json")
    secrets.set(cookie_id, "BDUSS=secret; STOKEN=hidden")
    assert secrets.get(cookie_id) == "BDUSS=secret; STOKEN=hidden"
    assert "secret" not in mask_cookie(secrets.get(cookie_id))
    assert "secret" not in redact_sensitive("BDUSS=secret; STOKEN=hidden")
    assert secrets.list_orphan_ids(store) == []
    secrets.set(cookie_id + 10, "BDUSS=orphan")
    assert secrets.list_orphan_ids(store) == [cookie_id + 10]
    store.close()


def test_environment_cookie_is_not_persisted(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BDPAN_COOKIE", "BDUSS=environment")
    app = create_app(tmp_path)
    with TestClient(app) as client:
        client.post("/api/login", json={
            "username": "admin", "password": "admin123",
        })
        settings = client.get("/api/settings").json()
        environment = next(item for item in settings["cookies"] if item["id"] == 0)
        assert environment["readonly"] is True
        assert "BDUSS=environment" not in json.dumps(settings)
    persisted = json.loads((tmp_path / "secrets.json").read_text(encoding="utf-8"))
    assert "BDUSS=environment" not in json.dumps(persisted)


def test_cookie_api_permissions_and_no_secret_leak(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    with TestClient(app) as client:
        client.post("/api/login", json={
            "username": "admin", "password": "admin123",
        })
        created = client.post("/api/cookies", json={
            "name": "主账号", "cookie": "BDUSS=top-secret; STOKEN=hidden",
        })
        assert created.status_code == 200
        cookie_id = created.json()["id"]
        response_text = client.get("/api/settings").text
        assert "top-secret" not in response_text
        assert "hidden" not in response_text
        duplicate = client.post("/api/cookies", json={
            "name": "主账号", "cookie": "BDUSS=other",
        })
        assert duplicate.status_code == 409

        client.post("/api/users", json={
            "username": "reader", "password": "reader123",
            "role": "user", "pages": ["settings"],
        })
        client.post("/api/logout")
        client.post("/api/login", json={
            "username": "reader", "password": "reader123",
        })
        assert client.get("/api/cookies").status_code == 200
        assert client.put(f"/api/cookies/{cookie_id}", json={
            "name": "改名",
        }).status_code == 403


def test_old_share_table_gets_cookie_column(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE share_links (id INTEGER PRIMARY KEY, url TEXT NOT NULL, "
        "surl TEXT NOT NULL, password TEXT NOT NULL DEFAULT '', "
        "title TEXT NOT NULL DEFAULT '', share_id INTEGER NOT NULL, "
        "share_uk INTEGER NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL, "
        "last_checked REAL NOT NULL, note TEXT NOT NULL DEFAULT '')"
    )
    conn.commit()
    conn.close()
    store = Database(str(path))
    columns = {
        row["name"] for row in store.conn.execute(
            "PRAGMA table_info(share_links)"
        ).fetchall()
    }
    assert "cookie_id" in columns
    store.close()
