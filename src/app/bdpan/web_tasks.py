"""后台同步任务与自动调度。"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from typing import Callable

from .client import BaiduPanClient, BaiduPanError
from .cookies import redact_sensitive
from .database import Database
from .models import ShareLink
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
    run_id: int | None = None
    has_files: bool = False


class TaskManager:
    """进程内任务队列；每个任务使用独立数据库连接。"""

    STANDARD_BACKOFF_SECONDS = 5 * 60
    STANDARD_BACKOFF_MAX_SECONDS = 6 * 60 * 60
    RISK_BACKOFF_SECONDS = 6 * 60 * 60
    RISK_BACKOFF_MAX_SECONDS = 24 * 60 * 60

    def __init__(
        self, db_path: str, cookie_getter: Callable[[ShareLink], str],
        cookie_updater: Callable[[ShareLink, str], None] | None = None,
        max_workers: int = 2, scheduler_poll_seconds: int = 15,
        history_retention_days: int = 90,
        history_max_runs_per_mapping: int = 200,
        scheduler_enabled: bool = True,
    ) -> None:
        self.db_path = db_path
        self.cookie_getter = cookie_getter
        self.cookie_updater = cookie_updater
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="bdpan")
        self.scheduler_poll_seconds = scheduler_poll_seconds
        self.history_retention_days = history_retention_days
        self.history_max_runs_per_mapping = history_max_runs_per_mapping
        self.tasks: dict[str, TaskState] = {}
        self._lock = threading.Lock()
        self._running_mappings: set[int] = set()
        self._stop = threading.Event()
        self._scheduler = threading.Thread(
            target=self._scheduler_loop, name="bdpan-scheduler", daemon=True
        )
        if scheduler_enabled:
            self._scheduler.start()

    def close(self) -> None:
        self._stop.set()
        self.executor.shutdown(wait=False, cancel_futures=False)

    def submit(
        self, kind: str, title: str, func: Callable[[Callable[[str], None]], str],
        on_created: Callable[[str], None] | None = None,
    ) -> str:
        task_id = uuid.uuid4().hex
        state = TaskState(task_id, kind, title, created_at=time.time())
        with self._lock:
            self.tasks[task_id] = state
        if on_created:
            on_created(task_id)

        def run() -> None:
            self._update(task_id, status="running", message="正在执行")
            try:
                message = func(lambda text: self._update(task_id, message=text))
                self._update(
                    task_id, status="success", message=message,
                    finished_at=time.time(),
                )
            except Exception as exc:
                safe_error = redact_sensitive(exc)
                logger.exception("后台任务失败: %s", title)
                self._update(
                    task_id, status="failed", message=safe_error,
                    finished_at=time.time(),
                )

        self.executor.submit(run)
        return task_id

    def submit_sync(self, mapping_id: int, trigger_type: str = "manual") -> str | None:
        with self._lock:
            if mapping_id in self._running_mappings:
                return None
            self._running_mappings.add(mapping_id)

        task_ref: dict[str, str] = {}

        def sync(progress: Callable[[str], None]) -> str:
            run_id: int | None = None
            mapping = None
            db = Database(self.db_path)
            try:
                mapping = db.get_sync_mapping(mapping_id)
                if not mapping:
                    raise ValueError("同步映射不存在")
                db.mark_sync_attempt(mapping_id, time.time())
                link = db.get_share_link(mapping.share_link_id)
                if not link:
                    raise ValueError("关联的分享链接不存在")
                entries = db.get_file_entries(mapping.share_link_id)
                run_id = db.add_sync_run(mapping_id, trigger_type)
                self._update(task_ref["id"], run_id=run_id)
                db.update_sync_run(run_id, "running", "正在检查目标存储")
                progress("正在检查目标存储连接")
                ensure_storage_ready(mapping.local_path, mapping.storage_type)
                try:
                    cookie_snapshot = self.cookie_getter(link)
                except TypeError:
                    # 兼容旧的嵌入式调用方；Web 与 CLI 均传入按链接解析函数。
                    cookie_snapshot = self.cookie_getter()  # type: ignore[call-arg]
                client = BaiduPanClient(cookie=cookie_snapshot)
                try:
                    client.prepare_share_download(link.url, link.password)
                    manager = SyncManager(
                        db, client.download_share_file,
                        progress_callback=lambda path: progress(f"正在处理：{path}"),
                    )
                    result = manager.sync_mapping(mapping, entries)
                finally:
                    if self.cookie_updater and client.cookie != cookie_snapshot:
                        self.cookie_updater(link, client.cookie)
                    client.close()
                message = result.summary()
                if result.errors:
                    message += "；" + "；".join(result.errors[:10])
                file_count = db.add_sync_file_events(
                    mapping_id, run_id, result.files_added, result.files_updated
                )
                self._update(task_ref["id"], has_files=file_count > 0)
                if result.errors:
                    db.update_sync_run(run_id, "failed", message)
                    raise RuntimeError(message)
                db.update_sync_run(
                    run_id, "success", message
                )
                return message
            except Exception as exc:
                if run_id is not None:
                    db.update_sync_run(run_id, "failed", redact_sensitive(exc))
                if mapping is not None:
                    failed_at = time.time()
                    delay = self._failure_backoff_seconds(
                        mapping.consecutive_failures + 1, exc
                    )
                    db.mark_sync_failure(
                        mapping_id, failed_at, failed_at + delay
                    )
                raise
            finally:
                try:
                    db.prune_sync_runs(
                        self.history_retention_days,
                        self.history_max_runs_per_mapping,
                    )
                except Exception:
                    logger.exception("同步历史清理失败")
                db.close()
                with self._lock:
                    self._running_mappings.discard(mapping_id)

        return self.submit(
            "sync", f"同步映射 #{mapping_id}", sync,
            on_created=lambda task_id: task_ref.update(id=task_id),
        )

    def list_tasks(self) -> list[dict]:
        with self._lock:
            states = sorted(
                (
                    item for item in self.tasks.values()
                    if item.status in {"queued", "running"}
                ),
                key=lambda item: item.created_at, reverse=True,
            )[:100]
            return [asdict(item) for item in states]

    def _update(self, task_id: str, **values: object) -> None:
        with self._lock:
            task = self.tasks.get(task_id)
            if task:
                for key, value in values.items():
                    setattr(task, key, value)
            completed = sorted(
                (
                    item for item in self.tasks.values()
                    if item.status in {"success", "failed"}
                ),
                key=lambda item: item.finished_at, reverse=True,
            )
            for stale in completed[200:]:
                self.tasks.pop(stale.id, None)

    @classmethod
    def _failure_backoff_seconds(cls, failure_count: int, exc: Exception) -> int:
        error: BaseException | None = exc
        risk_controlled = False
        visited: set[int] = set()
        while error is not None and id(error) not in visited:
            visited.add(id(error))
            if isinstance(error, BaiduPanError) and error.code == -65:
                risk_controlled = True
                break
            error = error.__cause__ or error.__context__
        exponent = min(16, max(0, failure_count - 1))
        if risk_controlled:
            return min(
                cls.RISK_BACKOFF_SECONDS * (2 ** exponent),
                cls.RISK_BACKOFF_MAX_SECONDS,
            )
        return min(
            cls.STANDARD_BACKOFF_SECONDS * (2 ** exponent),
            cls.STANDARD_BACKOFF_MAX_SECONDS,
        )

    def _scheduler_tick(self, now: float | None = None) -> list[int]:
        """执行一次调度检查，返回本轮提交的映射 ID。"""
        current = time.time() if now is None else now
        db = Database(self.db_path)
        try:
            due = [
                mapping for mapping in db.get_all_sync_mappings()
                if mapping.auto_sync
                and current - mapping.last_synced
                >= max(1, mapping.schedule_interval) * 60
                and current >= mapping.retry_after
            ]
        finally:
            db.close()
        submitted: list[int] = []
        for mapping in due:
            if mapping.id is not None and self.submit_sync(
                mapping.id, "scheduled"
            ) is not None:
                submitted.append(mapping.id)
        return submitted

    def _scheduler_loop(self) -> None:
        while not self._stop.wait(self.scheduler_poll_seconds):
            try:
                self._scheduler_tick()
            except Exception:
                logger.exception("自动同步调度检查失败")
