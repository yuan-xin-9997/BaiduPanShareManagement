"""本地文件夹同步逻辑。

负责将分享链接中的目录结构与本地文件夹关联并同步。
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from .models import FileEntry, ShareLink, SyncMapping
from .storage import ensure_storage_ready

logger = logging.getLogger(__name__)


class SyncStrategy(str, Enum):
    """同步策略。"""
    MIRROR = "mirror"      # 镜像：本地与远程完全一致（远程删除的本地也删）
    COPY_NEW = "copy_new"  # 仅复制新增：只增不删
    ASK = "ask"            # 每次询问用户


@dataclass
class SyncResult:
    """单次同步结果。"""
    mapping_id: int
    remote_path: str
    local_path: str
    files_added: list[str] = field(default_factory=list)
    files_updated: list[str] = field(default_factory=list)
    files_deleted: list[str] = field(default_factory=list)
    files_skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def total_changes(self) -> int:
        return len(self.files_added) + len(self.files_updated) + len(self.files_deleted)

    def summary(self) -> str:
        parts = []
        if self.files_added:
            parts.append(f"新增 {len(self.files_added)}")
        if self.files_updated:
            parts.append(f"更新 {len(self.files_updated)}")
        if self.files_deleted:
            parts.append(f"删除 {len(self.files_deleted)}")
        if self.errors:
            parts.append(f"错误 {len(self.errors)}")
        if not parts:
            return "无变化"
        return "，".join(parts)


class SyncManager:
    """管理远程分享目录与本地文件夹的同步。

    将分享目录中的真实文件下载到本地，并根据同步策略增量更新。
    """

    META_DIR = ".bdpan_meta"
    META_FILE = "link_info.json"

    def __init__(
        self,
        db,
        downloader: Callable[[int, str], int] | None = None,
        progress_callback: Callable[[str], None] | None = None,
    ):
        """初始化同步管理器。

        Args:
            db: Database 实例
        """
        self.db = db
        self.downloader = downloader
        self.progress_callback = progress_callback

    def sync_mapping(
        self,
        mapping: SyncMapping,
        file_entries: list[FileEntry],
        strategy: SyncStrategy | None = None,
        confirm_callback: Callable[[str, list[str]], bool] | None = None,
    ) -> SyncResult:
        """执行单条同步映射。

        Args:
            mapping: 同步映射配置
            file_entries: 该分享链接下的所有文件条目
            strategy: 覆盖映射自身的策略，None 则用映射配置
            confirm_callback: ASK 策略时的确认回调 (message, items) -> bool

        Returns:
            SyncResult 同步结果
        """
        strat = SyncStrategy(strategy or mapping.sync_strategy or SyncStrategy.COPY_NEW)
        local_root = Path(mapping.local_path)
        remote_root = mapping.remote_path.rstrip("/")

        # 筛选出属于该远程路径的文件
        relevant = [
            f for f in file_entries
            if self._is_under(f.path, remote_root)
        ]

        if not relevant:
            legacy_path = self._find_legacy_path(file_entries, remote_root)
            detail = (
                f"检测到旧索引路径 {legacy_path!r}。映射路径无需修改，"
                "请先在左侧选中分享链接并点击“刷新”，等待刷新完成后再同步。"
                if legacy_path else
                "请先刷新分享链接并确认映射路径。"
            )
            raise ValueError(
                f"远端路径 {mapping.remote_path!r} 未匹配到任何索引条目，已中止同步。"
                + detail
            )

        result = SyncResult(
            mapping_id=mapping.id or 0,
            remote_path=remote_root,
            local_path=str(local_root),
        )

        # SMB 挂载失效时必须在 mkdir 前中止，避免误写入本机系统盘。
        ensure_storage_ready(mapping.local_path, mapping.storage_type)
        # 确保目标目录存在
        local_root.mkdir(parents=True, exist_ok=True)
        local_existing = self._collect_local_files(local_root)
        managed_placeholders = {
            rel for rel, path in local_existing.items()
            if self._is_managed_placeholder(path)
        }
        # 先清理旧版本旁车；即使后续某个下载失败，也不会继续残留。
        self._cleanup_legacy_metadata(local_root)

        # 构建期望的文件列表（相对于 remote_root 的路径）
        expected: dict[str, FileEntry] = {}
        for entry in relevant:
            rel = self._relative_path(entry.path, remote_root)
            if rel:
                expected[rel] = entry

        # 构建本地已有文件列表（排除 meta 目录）
        # 新增和更新
        for rel, entry in expected.items():
            local_file = local_root / rel

            if entry.is_dir:
                local_file.mkdir(parents=True, exist_ok=True)
                continue

            if self.progress_callback:
                self.progress_callback(entry.path)

            # 文件：下载新增内容，或替换旧版本留下的 0B 占位文件。
            if rel not in local_existing:
                # 新文件
                if strat == SyncStrategy.ASK and confirm_callback:
                    if not confirm_callback(f"新增文件: {rel}", [rel]):
                        result.files_skipped.append(rel)
                        continue
                self._download_entry(local_file, entry)
                result.files_added.append(rel)
            else:
                # 已存在，以文件大小判断是否需要重新下载。
                current_size = self._io_path(local_file).stat().st_size
                if entry.size > 0 and current_size != entry.size:
                    if strat == SyncStrategy.ASK and confirm_callback:
                        if not confirm_callback(f"更新文件: {rel}", [rel]):
                            result.files_skipped.append(rel)
                            continue
                    self._download_entry(local_file, entry)
                    result.files_updated.append(rel)

        # 镜像模式：删除本地多余文件
        if strat == SyncStrategy.MIRROR:
            for rel, path in local_existing.items():
                if rel not in expected and rel in managed_placeholders:
                    try:
                        if confirm_callback and strat == SyncStrategy.ASK:
                            if not confirm_callback(f"删除文件: {rel}", [rel]):
                                continue
                        self._io_path(path).unlink()
                        meta = path.with_name(path.name + ".bdpan")
                        io_meta = self._io_path(meta)
                        if io_meta.exists():
                            io_meta.unlink()
                        result.files_deleted.append(rel)
                    except Exception as e:
                        result.errors.append(f"删除 {rel} 失败: {e}")

        # 更新最后同步时间
        mapping.last_synced = time.time()
        self.db.update_sync_mapping(mapping)

        logger.info(
            "同步完成 %s -> %s: %s",
            remote_root,
            local_root,
            result.summary(),
        )
        return result

    def sync_all(
        self,
        strategy: SyncStrategy | None = None,
        confirm_callback: Callable[[str, list[str]], bool] | None = None,
    ) -> list[SyncResult]:
        """同步所有映射。

        Returns:
            所有映射的同步结果列表
        """
        mappings = self.db.get_all_sync_mappings()
        results = []
        for m in mappings:
            entries = self.db.get_file_entries(m.share_link_id)
            result = self.sync_mapping(m, entries, strategy, confirm_callback)
            results.append(result)
        return results

    def preview_changes(
        self,
        mapping: SyncMapping,
        file_entries: list[FileEntry],
    ) -> dict[str, list[str]]:
        """预览同步会产生的变化（不实际执行）。

        Returns:
            {"add": [...], "update": [...], "delete": [...], "skip": [...]}
        """
        local_root = Path(mapping.local_path)
        remote_root = mapping.remote_path.rstrip("/")
        relevant = [
            f for f in file_entries
            if self._is_under(f.path, remote_root)
        ]

        expected: dict[str, FileEntry] = {}
        for entry in relevant:
            rel = self._relative_path(entry.path, remote_root)
            if rel:
                expected[rel] = entry

        local_existing = self._collect_local_files(local_root)

        add, update, delete, skip = [], [], [], []
        for rel, entry in expected.items():
            if entry.is_dir:
                continue
            if rel not in local_existing:
                add.append(rel)
            else:
                current_size = self._io_path(local_root / rel).stat().st_size
                if entry.size > 0 and current_size != entry.size:
                    update.append(rel)

        if SyncStrategy(mapping.sync_strategy or SyncStrategy.COPY_NEW) == SyncStrategy.MIRROR:
            for rel, path in local_existing.items():
                if rel not in expected and self._is_managed_placeholder(path):
                    delete.append(rel)

        if not relevant:
            legacy_path = self._find_legacy_path(file_entries, remote_root)
            if legacy_path:
                skip.append(
                    f"当前数据库仍是旧索引路径 {legacy_path!r}。\n"
                    "映射路径无需修改；请在左侧选中分享链接，点击工具栏“刷新”，"
                    "并等待刷新完成后再同步。"
                )
            else:
                skip.append(
                    f"远端路径 {mapping.remote_path!r} 未匹配到任何索引条目；"
                    "请先刷新并检查路径"
                )

        return {"add": add, "update": update, "delete": delete, "skip": skip}

    # ------------------------------------------------------------------
    # 内部工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _is_under(path: str, root: str) -> bool:
        """path 是否在 root 目录下（或等于 root）。"""
        if not root:
            return True
        path = path.strip("/")
        root = root.strip("/")
        return path == root or path.startswith(root + "/")

    @staticmethod
    def _find_legacy_path(file_entries: list[FileEntry], root: str) -> str | None:
        """查找带百度内部前缀、但后缀与映射路径一致的旧索引目录。"""
        suffix = "/" + root.strip("/")
        matches = {
            entry.path
            for entry in file_entries
            if entry.is_dir and entry.path.rstrip("/").endswith(suffix)
        }
        return next(iter(matches)) if len(matches) == 1 else None

    @staticmethod
    def _relative_path(path: str, root: str) -> str:
        """获取相对于 root 的路径。"""
        path = path.strip("/")
        root = root.strip("/")
        if not root:
            return path
        if path == root:
            return ""
        if path.startswith(root + "/"):
            return path[len(root) + 1:]
        return ""

    @classmethod
    def _is_metadata_path(cls, path: Path) -> bool:
        return (
            cls.META_DIR in path.parts
            or path.name.endswith(".bdpan")
            or path.name.endswith(".bdpan.part")
        )

    @staticmethod
    def _is_managed_placeholder(path: Path) -> bool:
        meta = path.with_name(path.name + ".bdpan")
        io_path = SyncManager._io_path(path)
        io_meta = SyncManager._io_path(meta)
        return io_path.stat().st_size == 0 and io_meta.is_file()

    @staticmethod
    def _io_path(path: Path) -> Path:
        """返回支持 Windows 超过 260 字符路径的文件系统路径。"""
        if os.name != "nt":
            return path
        value = str(path.resolve())
        if value.startswith("\\\\?\\"):
            return Path(value)
        if value.startswith("\\\\"):
            return Path("\\\\?\\UNC\\" + value[2:])
        return Path("\\\\?\\" + value)

    @classmethod
    def _collect_local_files(cls, local_root: Path) -> dict[str, Path]:
        """扫描本地文件，并兼容 Windows 扩展长路径。"""
        if not local_root.exists():
            return {}
        io_root = cls._io_path(local_root)
        result: dict[str, Path] = {}
        for dirpath, dirnames, filenames in os.walk(io_root):
            dirnames[:] = [name for name in dirnames if name != cls.META_DIR]
            relative_dir = Path(dirpath).relative_to(io_root)
            for filename in filenames:
                relative = relative_dir / filename
                normal_path = local_root / relative
                if not cls._is_metadata_path(normal_path):
                    result[relative.as_posix()] = normal_path
        return result

    def _download_entry(self, local_file: Path, entry: FileEntry) -> None:
        """下载到临时文件，校验大小后原子替换正式文件。"""
        if not self.downloader:
            raise RuntimeError("同步器未配置百度网盘下载会话")
        io_file = self._io_path(local_file)
        io_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file = local_file.parent / f".{entry.fs_id}.bdpan.part"
        io_temp = self._io_path(temp_file)
        try:
            if io_temp.exists():
                io_temp.unlink()
            written = self.downloader(entry.fs_id, str(io_temp))
            if entry.size > 0 and written != entry.size:
                raise IOError(
                    f"下载大小不一致: 期望 {entry.size} 字节，实际 {written} 字节"
                )
            os.replace(io_temp, io_file)
        except Exception:
            if io_temp.exists():
                io_temp.unlink()
            raise

    @classmethod
    def _cleanup_legacy_metadata(cls, local_root: Path) -> None:
        """删除旧版本生成的 .bdpan 旁车、临时文件和元数据目录。"""
        io_root = cls._io_path(local_root)
        if not io_root.exists():
            return
        for dirpath, dirnames, filenames in os.walk(io_root, topdown=False):
            for filename in filenames:
                if filename.endswith(".bdpan") or filename.endswith(".bdpan.part"):
                    (Path(dirpath) / filename).unlink(missing_ok=True)
            for dirname in dirnames:
                if dirname == cls.META_DIR:
                    shutil.rmtree(Path(dirpath) / dirname, ignore_errors=True)
