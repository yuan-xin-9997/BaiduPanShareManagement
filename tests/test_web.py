from pathlib import Path
import sqlite3
import os

from fastapi.testclient import TestClient

from bdpan.web import create_app
from bdpan.database import Database
from bdpan.storage import SMB_MOUNT, probe_storage, validate_storage_path


def test_password_file_login_and_protected_state(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/bootstrap").json()["authenticated"] is False
        response = client.post("/api/login", json={
            "username": "admin", "password": "admin123",
        })
        assert response.status_code == 200
        assert response.json()["user"]["role"] == "admin"
        assert client.get("/api/state").status_code == 200
        client.post("/api/logout")
        assert client.get("/api/state").status_code == 401
        assert client.post("/api/login", json={
            "username": "admin", "password": "wrong-pass",
        }).status_code == 401
        assert client.post("/api/login", json={
            "username": "admin", "password": "admin123",
        }).status_code == 200


def test_mapping_requires_server_absolute_path(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    with TestClient(app) as client:
        client.post("/api/login", json={
            "username": "admin", "password": "admin123",
        })
        response = client.post("/api/mappings", json={
            "share_link_id": 1,
            "remote_path": "/reports",
            "local_path": "relative/path",
            "sync_strategy": "copy_new",
            "schedule_interval": 60,
        })
        assert response.status_code == 400
        assert "绝对路径" in response.json()["detail"]


def test_regular_user_is_limited_by_page_permissions(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    with TestClient(app) as client:
        client.post("/api/login", json={"username": "admin", "password": "admin123"})
        response = client.post("/api/users", json={
            "username": "reader", "password": "reader123",
            "role": "user", "pages": ["shares"],
        })
        assert response.status_code == 200
        client.post("/api/logout")
        assert client.post("/api/login", json={
            "username": "reader", "password": "reader123",
        }).status_code == 200
        assert client.get("/api/state").status_code == 200
        assert client.get("/api/settings").status_code == 403
        assert client.get("/api/users").status_code == 403
        assert client.post("/api/mappings", json={}).status_code == 403


def test_database_migrates_existing_mapping_table(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE sync_mappings (id INTEGER PRIMARY KEY, "
        "share_link_id INTEGER NOT NULL, remote_path TEXT NOT NULL, "
        "local_path TEXT NOT NULL, auto_sync INTEGER NOT NULL, "
        "last_synced REAL NOT NULL, sync_strategy TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()

    store = Database(str(path))
    columns = {
        row["name"] for row in store.conn.execute(
            "PRAGMA table_info(sync_mappings)"
        ).fetchall()
    }
    store.close()
    assert {"schedule_interval", "storage_type"}.issubset(columns)


def test_local_storage_probe_accepts_existing_writable_directory(tmp_path: Path) -> None:
    result = probe_storage(str(tmp_path / "new-sync-folder"), "local")
    assert result["ok"] is True


def test_windows_smb_path_accepts_unc_and_rejects_local_drive() -> None:
    if os.name != "nt":
        return
    assert validate_storage_path(
        r"\\192.168.0.100\documents\reports", SMB_MOUNT
    ).startswith(r"\\192.168.0.100")
    try:
        validate_storage_path(r"E:\reports", SMB_MOUNT)
    except ValueError as exc:
        assert "UNC" in str(exc)
    else:
        raise AssertionError("本地盘符不应被标记为 SMB 存储")
