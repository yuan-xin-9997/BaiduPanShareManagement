from pathlib import Path
import json
import sqlite3
import os

from fastapi.testclient import TestClient

from bdpan.web import create_app
from bdpan.database import Database
from bdpan.storage import SMB_MOUNT, probe_storage, validate_storage_path


def test_default_admin_login_and_protected_state(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    with TestClient(app) as client:
        assert not (tmp_path / "password.txt").exists()
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
        assert not (tmp_path / "password.txt").exists()


def test_legacy_password_file_is_imported_and_removed(tmp_path: Path) -> None:
    (tmp_path / "password.txt").write_text(
        "# legacy\nlegacy:legacy123:user\n", encoding="utf-8"
    )
    app = create_app(tmp_path)
    with TestClient(app) as client:
        assert client.post("/api/login", json={
            "username": "legacy", "password": "legacy123",
        }).status_code == 200
        client.post("/api/logout")
        # 旧文件没有管理员时，启动会补建默认管理员。
        assert client.post("/api/login", json={
            "username": "admin", "password": "admin123",
        }).status_code == 200
    assert not (tmp_path / "password.txt").exists()


def test_users_persist_in_database_without_password_file(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    with TestClient(app) as client:
        client.post("/api/login", json={
            "username": "admin", "password": "admin123",
        })
        assert client.post("/api/users", json={
            "username": "persisted", "password": "persisted123",
            "role": "user", "pages": ["shares"],
        }).status_code == 200
    assert not (tmp_path / "password.txt").exists()

    restarted = create_app(tmp_path)
    with TestClient(restarted) as client:
        assert client.post("/api/login", json={
            "username": "persisted", "password": "persisted123",
        }).status_code == 200


def test_account_password_change(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    with TestClient(app) as client:
        client.post("/api/login", json={
            "username": "admin", "password": "admin123",
        })
        assert client.post("/api/account/password", json={
            "current_password": "wrong-pass", "new_password": "newpass123",
        }).status_code == 400
        assert client.post("/api/account/password", json={
            "current_password": "admin123", "new_password": "short",
        }).status_code == 400
        assert client.post("/api/account/password", json={
            "current_password": "admin123", "new_password": "newpass123",
        }).status_code == 200
        client.post("/api/logout")
        assert client.post("/api/login", json={
            "username": "admin", "password": "admin123",
        }).status_code == 401
        assert client.post("/api/login", json={
            "username": "admin", "password": "newpass123",
        }).status_code == 200


def test_admin_can_update_runtime_settings(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    with TestClient(app) as client:
        client.post("/api/login", json={
            "username": "admin", "password": "admin123",
        })
        response = client.put("/api/settings", json={
            "app_name": "测试分享管理",
            "log_retention_days": 7,
            "task_workers": 4,
            "scheduler_poll_seconds": 30,
            "sync_history_retention_days": 30,
            "sync_history_max_runs_per_mapping": 100,
        })
        assert response.status_code == 200
        assert response.json()["restart_required"] == ["task_workers"]
        payload = client.get("/api/settings").json()["config"]
        assert payload["editable"]["app_name"] == "测试分享管理"
        assert payload["editable"]["log_retention_days"] == 7
        assert payload["editable"]["task_workers"] == 4
        saved = json.loads((tmp_path / "app.json").read_text(encoding="utf-8"))
        assert saved["app_name"] == "测试分享管理"
        assert saved["sync_history_retention_days"] == 30
        assert app.title == "测试分享管理"
        assert app.state.tasks.scheduler_poll_seconds == 30
        assert app.state.tasks.history_retention_days == 30
        assert app.state.tasks.history_max_runs_per_mapping == 100


def test_invalid_settings_are_rejected(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    with TestClient(app) as client:
        client.post("/api/login", json={
            "username": "admin", "password": "admin123",
        })
        assert client.put("/api/settings", json={
            "scheduler_poll_seconds": 1,
        }).status_code == 400
        assert client.put("/api/settings", json={
            "app_name": "   ",
        }).status_code == 400
        assert client.put("/api/settings", json={
            "host": "0.0.0.0",
        }).status_code == 400


def test_favicon_served_for_browser_tab(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    with TestClient(app) as client:
        ico = client.get("/favicon.ico")
        assert ico.status_code == 200
        assert ico.headers["content-type"].startswith("image/")
        svg = client.get("/favicon.svg")
        assert svg.status_code == 200
        assert svg.headers["content-type"].startswith("image/svg")
        assert '<link rel="icon" href="/favicon.svg"' in client.get("/").text


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
        assert client.put("/api/settings", json={"app_name": "x"}).status_code == 403
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
    assert {
        "schedule_interval", "storage_type", "last_attempted",
        "retry_after", "consecutive_failures",
    }.issubset(columns)


def test_local_storage_probe_accepts_existing_writable_directory(tmp_path: Path) -> None:
    result = probe_storage(str(tmp_path / "new-sync-folder"), "local")
    assert result["ok"] is True


def test_storage_probe_reads_directory_to_trigger_lazy_mount(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "nas"
    target.mkdir()
    scanned: list[str] = []
    real_scandir = os.scandir

    def recording_scandir(path):
        scanned.append(str(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", recording_scandir)
    result = probe_storage(str(target), "local")
    assert result["ok"] is True
    assert str(target) in scanned


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
