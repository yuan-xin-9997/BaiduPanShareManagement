"""后台同步任务与自动调度。"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from typing import Callable

from .client import BaiduPanClient
from .database import Database
from .sync import SyncManager
from .storage import ensure_storage_ready

logger = logging.getLogger(__name__)


@dataclass
class TaskState:
    id: str
    kind: str
    title: str
    status: str = "queued"
    message: str = "等待执行"
    created_at: float = 0
    finished_at: float = 0


class TaskManager:
    """进程内任务队列；每个任务使用独立数据库连接。"""

    def __init__(
        self, db_path: str, cookie_getter: Callable[[], str],
        max_workers: int = 2, scheduler_poll_seconds: int = 15,
    ) -> None:
        self.db_path = db_path
        self.cookie_getter = cookie_getter
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="bdpan")
        self.scheduler_poll_seconds = scheduler_poll_seconds
        self.tasks: dict[str, TaskState] = {}
        self._lock = threading.Lock()
        self._running_mappings: set[int] = set()
        self._stop = threading.Event()
        self._scheduler = threading.Thread(
            target=self._scheduler_loop, name="bdpan-scheduler", daemon=True
        )
        self._scheduler.start()

    def close(self) -> None:
        self._stop.set()
        self.executor.shutdown(wait=False, cancel_futures=False)

    def submit(
        self, kind: str, title: str, func: Callable[[Callable[[str], None]], str]
    ) -> str:
        task_id = uuid.uuid4().hex
        state = TaskState(task_id, kind, title, created_at=time.time())
        with self._lock:
            self.tasks[task_id] = state

        def run() -> None:
            self._update(task_id, status="running", message="正在执行")
            try:
                message = func(lambda text: self._update(task_id, message=text))
                self._update(
                    task_id, status="success", message=message,
                    finished_at=time.time(),
                )
            except Exception as exc:
                logger.exception("后台任务失败: %s", title)
                self._update(
                    task_id, status="failed", message=str(exc),
                    finished_at=time.time(),
                )

        self.executor.submit(run)
        return task_id

    def submit_sync(self, mapping_id: int, trigger_type: str = "manual") -> str | None:
        with self._lock:
            if mapping_id in self._running_mappings:
                return None
            self._running_mappings.add(mapping_id)

        def sync(progress: Callable[[str], None]) -> str:
            run_id: int | None = None
            db = Database(self.db_path)
            try:
                mapping = db.get_sync_mapping(mapping_id)
                if not mapping:
                    raise ValueError("同步映射不存在")
                link = db.get_share_link(mapping.share_link_id)
                if not link:
                    raise ValueError("关联的分享链接不存在")
                entries = db.get_file_entries(mapping.share_link_id)
                run_id = db.add_sync_run(mapping_id, trigger_type)
                db.update_sync_run(run_id, "running", "正在检查目标存储")
                progress("正在检查目标存储连接")
                ensure_storage_ready(mapping.local_path, mapping.storage_type)
                client = BaiduPanClient(cookie=self.cookie_getter())
                try:
                    client.prepare_share_download(link.url, link.password)
                    manager = SyncManager(
                        db, client.download_share_file,
                        progress_callback=lambda path: progress(f"正在处理：{path}"),
                    )
                    result = manager.sync_mapping(mapping, entries)
                finally:
                    client.close()
                message = result.summary()
                if result.errors:
                    message += "；" + "；".join(result.errors[:10])
                db.update_sync_run(
                    run_id, "failed" if result.errors else "success", message
                )
                if result.errors:
                    raise RuntimeError(message)
                return message
            except Exception as exc:
                if run_id is not None:
                    db.update_sync_run(run_id, "failed", str(exc))
                raise
            finally:
                db.close()
                with self._lock:
                    self._running_mappings.discard(mapping_id)

        return self.submit("sync", f"同步映射 #{mapping_id}", sync)

    def list_tasks(self) -> list[dict]:
        with self._lock:
            states = sorted(
                self.tasks.values(), key=lambda item: item.created_at, reverse=True
            )[:100]
            return [asdict(item) for item in states]

    def _update(self, task_id: str, **values: object) -> None:
        with self._lock:
            task = self.tasks.get(task_id)
            if task:
                for key, value in values.items():
                    setattr(task, key, value)

    def _scheduler_loop(self) -> None:
        while not self._stop.wait(self.scheduler_poll_seconds):
            try:
                db = Database(self.db_path)
                try:
                    now = time.time()
                    due = [
                        mapping for mapping in db.get_all_sync_mappings()
                        if mapping.auto_sync
                        and now - mapping.last_synced
                        >= max(1, mapping.schedule_interval) * 60
                    ]
                finally:
                    db.close()
                for mapping in due:
                    if mapping.id is not None:
                        self.submit_sync(mapping.id, "scheduled")
            except Exception:
                logger.exception("自动同步调度检查失败")
