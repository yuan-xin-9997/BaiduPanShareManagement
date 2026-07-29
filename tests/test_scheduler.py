from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import Mock, patch

from bdpan.client import BaiduPanError
from bdpan.database import Database
from bdpan.models import ShareLink, SyncMapping
from bdpan.web_tasks import TaskManager


def _seed_auto_mapping(db_path: Path, local_path: Path) -> int:
    with Database(str(db_path)) as store:
        link_id = store.add_share_link(ShareLink(
            None, "https://pan.baidu.com/s/example", "example", "6666", "资料",
            1, 2, "active", time.time(), time.time(), "",
        ))
        return store.add_sync_mapping(SyncMapping(
            None, link_id, "/资料", str(local_path), True, 0,
            "copy_new", 1440, "local",
        ))


def _wait_for_terminal(manager: TaskManager, task_id: str) -> None:
    deadline = time.time() + 3
    while manager.tasks[task_id].status not in {"success", "failed"}:
        assert time.time() < deadline
        time.sleep(0.01)


def test_failed_sync_is_not_resubmitted_until_backoff_expires(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "app.sqlite3"
    mapping_id = _seed_auto_mapping(db_path, tmp_path / "target")
    client = Mock()
    client.prepare_share_download.side_effect = BaiduPanError(-65)
    manager = TaskManager(
        str(db_path), lambda _link: "BDUSS=test", scheduler_enabled=False,
    )
    try:
        with (
            patch("bdpan.web_tasks.ensure_storage_ready"),
            patch("bdpan.web_tasks.BaiduPanClient", return_value=client),
        ):
            task_id = manager.submit_sync(mapping_id, "scheduled")
            assert task_id is not None
            _wait_for_terminal(manager, task_id)

        with Database(str(db_path)) as store:
            mapping = store.get_sync_mapping(mapping_id)
            assert mapping is not None
            assert mapping.consecutive_failures == 1
            assert mapping.retry_after - mapping.last_attempted >= 6 * 60 * 60

        submit = Mock(return_value="scheduled-task")
        manager.submit_sync = submit
        assert manager._scheduler_tick(mapping.retry_after - 1) == []
        submit.assert_not_called()

        assert manager._scheduler_tick(mapping.retry_after) == [mapping_id]
        submit.assert_called_once_with(mapping_id, "scheduled")
    finally:
        manager.close()


def test_standard_failure_uses_shorter_backoff() -> None:
    delay = TaskManager._failure_backoff_seconds(1, RuntimeError("disk error"))
    assert delay == 5 * 60
    assert TaskManager._failure_backoff_seconds(2, RuntimeError()) == 10 * 60


def test_sync_history_prunes_age_and_per_mapping_limit(tmp_path: Path) -> None:
    db_path = tmp_path / "app.sqlite3"
    mapping_id = _seed_auto_mapping(db_path, tmp_path / "target")
    with Database(str(db_path)) as store:
        old = store.add_sync_run(mapping_id, "scheduled")
        store.update_sync_run(old, "failed", "old")
        store.conn.execute(
            "UPDATE sync_runs SET started_at = 1, finished_at = 1 WHERE id = ?",
            (old,),
        )
        for _ in range(4):
            run_id = store.add_sync_run(mapping_id, "scheduled")
            store.update_sync_run(run_id, "failed", "recent")

        deleted = store.prune_sync_runs(
            retention_days=90, max_per_mapping=2, now=time.time(),
        )
        remaining = store.list_sync_runs(limit=10)

    assert deleted == 3
    assert len(remaining) == 2
