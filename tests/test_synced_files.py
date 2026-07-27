from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from bdpan.database import Database
from bdpan.models import ShareLink, SyncMapping
from bdpan.sync import SyncResult
from bdpan.web import create_app
from bdpan.web_tasks import TaskManager


def _seed_mapping(store: Database, local_path: Path) -> int:
    link_id = store.add_share_link(ShareLink(
        None, "https://pan.baidu.com/s/example", "example", "", "资料",
        1, 2, "active", time.time(), time.time(), "",
    ))
    return store.add_sync_mapping(SyncMapping(
        None, link_id, "/资料", str(local_path), False, 0,
        "copy_new", 60, "local",
    ))


def test_sync_file_schema_migrates_without_changing_legacy_data(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE sync_mappings (id INTEGER PRIMARY KEY, "
        "share_link_id INTEGER NOT NULL, remote_path TEXT NOT NULL, "
        "local_path TEXT NOT NULL, auto_sync INTEGER NOT NULL, "
        "last_synced REAL NOT NULL, sync_strategy TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO sync_mappings VALUES "
        "(7, 3, '/remote', '/local', 0, 123, 'copy_new')"
    )
    conn.commit()
    conn.close()

    store = Database(str(path))
    table = store.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='sync_file_events'"
    ).fetchone()
    legacy = store.conn.execute(
        "SELECT id, remote_path, local_path FROM sync_mappings WHERE id=7"
    ).fetchone()
    indexes = {
        row["name"] for row in store.conn.execute(
            "PRAGMA index_list(sync_file_events)"
        ).fetchall()
    }
    store.close()

    assert table is not None
    assert tuple(legacy) == (7, "/remote", "/local")
    assert {
        "idx_sync_file_events_mapping_path",
        "idx_sync_file_events_run",
        "idx_sync_file_events_file_name",
        "idx_sync_file_events_synced_at",
    }.issubset(indexes)


def test_sync_file_queries_deduplicate_and_sort_safely(tmp_path: Path) -> None:
    store = Database(str(tmp_path / "app.sqlite3"))
    mapping_id = _seed_mapping(store, tmp_path / "target")
    first_run = store.add_sync_run(mapping_id, "manual")
    assert store.add_sync_file_events(
        mapping_id, first_run,
        ["z/report.pdf", "a/same.txt", "b/same.txt"], [],
    ) == 3
    store.conn.execute(
        "UPDATE sync_file_events SET synced_at = CASE relative_path "
        "WHEN 'z/report.pdf' THEN 100 WHEN 'a/same.txt' THEN 200 ELSE 300 END"
    )
    store.conn.commit()
    second_run = store.add_sync_run(mapping_id, "manual")
    store.add_sync_file_events(mapping_id, second_run, [], ["z/report.pdf"])
    store.conn.execute(
        "UPDATE sync_file_events SET synced_at = 400 WHERE run_id = ?",
        (second_run,),
    )
    store.conn.commit()

    by_name = store.list_mapping_synced_files(mapping_id, "file_name", "asc")
    by_time = store.list_mapping_synced_files(mapping_id, "synced_at", "desc")
    run_files = store.list_run_synced_files(first_run, "file_name", "desc")

    assert [item["relative_path"] for item in by_name] == [
        "z/report.pdf", "a/same.txt", "b/same.txt",
    ]
    assert [item["relative_path"] for item in by_time] == [
        "z/report.pdf", "b/same.txt", "a/same.txt",
    ]
    assert [item["relative_path"] for item in run_files] == [
        "b/same.txt", "a/same.txt", "z/report.pdf",
    ]
    assert by_time[0]["action"] == "updated"
    for sort_by, direction in (("drop table", "asc"), ("file_name", "sideways")):
        try:
            store.list_mapping_synced_files(mapping_id, sort_by, direction)
        except ValueError as exc:
            assert "排序参数无效" in str(exc)
        else:
            raise AssertionError("非法排序参数必须被拒绝")
    store.close()


def test_task_manager_records_files_and_hides_completed_tasks(tmp_path: Path) -> None:
    db_path = tmp_path / "app.sqlite3"
    with Database(str(db_path)) as store:
        mapping_id = _seed_mapping(store, tmp_path / "target")
    result = SyncResult(mapping_id, "/资料", str(tmp_path / "target"))
    result.files_added = ["new.txt"]
    result.files_updated = ["folder/updated.txt"]
    client = MagicMock()
    sync_manager = MagicMock()
    sync_manager.sync_mapping.return_value = result
    resolved_links = []

    with (
        patch("bdpan.web_tasks.ensure_storage_ready"),
        patch("bdpan.web_tasks.BaiduPanClient", return_value=client) as client_factory,
        patch("bdpan.web_tasks.SyncManager", return_value=sync_manager),
    ):
        manager = TaskManager(
            str(db_path),
            lambda link: resolved_links.append(link) or "cookie-for-link",
        )
        task_id = manager.submit_sync(mapping_id)
        assert task_id
        deadline = time.time() + 3
        while manager.tasks[task_id].status not in {"success", "failed"}:
            assert time.time() < deadline
            time.sleep(0.01)
        assert manager.tasks[task_id].status == "success"
        assert manager.tasks[task_id].run_id is not None
        assert manager.tasks[task_id].has_files is True
        assert manager.list_tasks() == []
        manager.close()
        assert len(resolved_links) == 1
        assert resolved_links[0].id is not None
        client_factory.assert_called_once_with(cookie="cookie-for-link")

    with Database(str(db_path)) as store:
        runs = store.list_sync_runs()
        assert runs[0]["status"] == "success"
        assert runs[0]["has_files"] is True
        assert {
            item["relative_path"]
            for item in store.list_run_synced_files(runs[0]["id"])
        } == {"new.txt", "folder/updated.txt"}


def test_task_manager_records_only_successful_files_from_partially_failed_result(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "app.sqlite3"
    with Database(str(db_path)) as store:
        mapping_id = _seed_mapping(store, tmp_path / "target")
    result = SyncResult(mapping_id, "/资料", str(tmp_path / "target"))
    result.files_added = ["downloaded-before-error.txt"]
    result.errors = ["另一个文件下载失败"]
    sync_manager = MagicMock()
    sync_manager.sync_mapping.return_value = result

    with (
        patch("bdpan.web_tasks.ensure_storage_ready"),
        patch("bdpan.web_tasks.BaiduPanClient", return_value=MagicMock()),
        patch("bdpan.web_tasks.SyncManager", return_value=sync_manager),
    ):
        manager = TaskManager(str(db_path), lambda: "cookie")
        task_id = manager.submit_sync(mapping_id)
        assert task_id
        deadline = time.time() + 3
        while manager.tasks[task_id].status not in {"success", "failed"}:
            assert time.time() < deadline
            time.sleep(0.01)
        assert manager.tasks[task_id].status == "failed"
        assert manager.tasks[task_id].has_files is True
        assert manager.list_tasks() == []
        manager.close()

    with Database(str(db_path)) as store:
        run = store.list_sync_runs()[0]
        assert run["status"] == "failed"
        assert run["has_files"] is True
        assert [
            item["relative_path"]
            for item in store.list_run_synced_files(run["id"])
        ] == ["downloaded-before-error.txt"]


def test_synced_file_api_data_empty_permissions_and_sort_validation(
    tmp_path: Path,
) -> None:
    app = create_app(tmp_path)
    with TestClient(app) as client:
        assert client.post("/api/login", json={
            "username": "admin", "password": "admin123",
        }).status_code == 200
        with Database(str(tmp_path / "app.sqlite3")) as store:
            mapping_id = _seed_mapping(store, tmp_path / "target")
            old_run = store.add_sync_run(mapping_id, "manual")
            run_id = store.add_sync_run(mapping_id, "manual")
            store.add_sync_file_events(
                mapping_id, run_id, ["b.txt", "folder/a.txt"], []
            )

        mapping_files = client.get(
            f"/api/mappings/{mapping_id}/files",
            params={"sort_by": "file_name", "direction": "asc"},
        )
        assert mapping_files.status_code == 200
        assert [item["file_name"] for item in mapping_files.json()] == ["a.txt", "b.txt"]
        assert "local_path" not in mapping_files.json()[0]
        assert client.get(f"/api/sync-runs/{old_run}/files").json() == []
        assert len(client.get(f"/api/sync-runs/{run_id}/files").json()) == 2
        assert client.get("/api/mappings/99999/files").status_code == 404
        assert client.get("/api/sync-runs/99999/files").status_code == 404
        assert client.get(
            f"/api/mappings/{mapping_id}/files?sort_by=unsafe"
        ).status_code == 400
        state = client.get("/api/state").json()
        history = {item["id"]: item for item in state["runs"]}
        assert history[run_id]["has_files"] is True
        assert history[old_run]["has_files"] is False

        assert client.post("/api/users", json={
            "username": "mappinguser", "password": "mapping123",
            "role": "user", "pages": ["mappings"],
        }).status_code == 200
        client.post("/api/logout")
        client.post("/api/login", json={
            "username": "mappinguser", "password": "mapping123",
        })
        assert client.get(f"/api/mappings/{mapping_id}/files").status_code == 200
        assert client.get(f"/api/sync-runs/{run_id}/files").status_code == 403
