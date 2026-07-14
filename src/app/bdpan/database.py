"""SQLite 存储层。

使用 Python 内置 sqlite3 模块实现 Database 类，负责对
ShareLink / FileEntry / SyncMapping 三个实体的持久化与查询。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .models import FileEntry, ShareLink, SyncMapping, User

# 建表 SQL：表结构稳定，使用 IF NOT EXISTS 保证幂等。
_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS user_page_permissions (
    user_id       INTEGER NOT NULL,
    page          TEXT NOT NULL,
    PRIMARY KEY (user_id, page),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS share_links (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    url           TEXT    NOT NULL,
    surl          TEXT    NOT NULL,
    password      TEXT    NOT NULL DEFAULT '',
    title         TEXT    NOT NULL DEFAULT '',
    share_id      INTEGER NOT NULL,
    share_uk      INTEGER NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'active',
    created_at    REAL    NOT NULL,
    last_checked  REAL    NOT NULL,
    note          TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS file_entries (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    share_link_id  INTEGER NOT NULL,
    fs_id          INTEGER NOT NULL,
    name           TEXT    NOT NULL,
    path           TEXT    NOT NULL,
    is_dir         INTEGER NOT NULL DEFAULT 0,
    size           INTEGER NOT NULL DEFAULT 0,
    md5            TEXT    NOT NULL DEFAULT '',
    modified_time  INTEGER NOT NULL DEFAULT 0,
    local_path     TEXT,
    FOREIGN KEY (share_link_id) REFERENCES share_links(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sync_mappings (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    share_link_id  INTEGER NOT NULL,
    remote_path    TEXT    NOT NULL,
    local_path     TEXT    NOT NULL,
    auto_sync      INTEGER NOT NULL DEFAULT 0,
    last_synced    REAL    NOT NULL,
    sync_strategy  TEXT    NOT NULL DEFAULT 'ask',
    schedule_interval INTEGER NOT NULL DEFAULT 60,
    storage_type   TEXT    NOT NULL DEFAULT 'local',
    FOREIGN KEY (share_link_id) REFERENCES share_links(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    mapping_id    INTEGER,
    trigger_type  TEXT NOT NULL DEFAULT 'manual',
    status        TEXT NOT NULL DEFAULT 'queued',
    message       TEXT NOT NULL DEFAULT '',
    started_at    REAL NOT NULL,
    finished_at   REAL NOT NULL DEFAULT 0,
    FOREIGN KEY (mapping_id) REFERENCES sync_mappings(id) ON DELETE SET NULL
);
"""

# 开启外键支持（SQLite 默认关闭，必须显式打开 CASCADE 才生效）。
_ENABLE_FK = "PRAGMA foreign_keys = ON;"


class Database:
    """百度网盘分享链接管理工具的 SQLite 数据访问层。

    支持作为上下文管理器使用::

        with Database("bdpan.db") as db:
            db.add_share_link(link)
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        # check_same_thread=False 允许跨线程共享连接；
        # 调用方需自行保证并发安全或使用独立连接。
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(_ENABLE_FK)
        self._init_schema()

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def _init_schema(self) -> None:
        """创建表结构（幂等）。"""
        self.conn.executescript(_SCHEMA)
        columns = {
            row["name"] for row in self.conn.execute(
                "PRAGMA table_info(sync_mappings)"
            ).fetchall()
        }
        if "schedule_interval" not in columns:
            self.conn.execute(
                "ALTER TABLE sync_mappings ADD COLUMN "
                "schedule_interval INTEGER NOT NULL DEFAULT 60"
            )
        if "storage_type" not in columns:
            self.conn.execute(
                "ALTER TABLE sync_mappings ADD COLUMN "
                "storage_type TEXT NOT NULL DEFAULT 'local'"
            )
        self.conn.commit()

    def close(self) -> None:
        """关闭数据库连接。"""
        self.conn.close()

    # ------------------------------------------------------------------ #
    # User / permission CRUD
    # ------------------------------------------------------------------ #
    def get_user_by_username(self, username: str) -> User | None:
        row = self.conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        return self._row_to_user(row) if row else None

    def get_user(self, user_id: int) -> User | None:
        row = self.conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return self._row_to_user(row) if row else None

    def list_users(self) -> list[User]:
        rows = self.conn.execute(
            "SELECT * FROM users ORDER BY username"
        ).fetchall()
        return [self._row_to_user(row) for row in rows]

    def upsert_user(self, username: str, password_hash: str, role: str) -> int:
        now = __import__("time").time()
        self.conn.execute(
            "INSERT INTO users "
            "(username, password_hash, role, active, created_at, updated_at) "
            "VALUES (?, ?, ?, 1, ?, ?) "
            "ON CONFLICT(username) DO UPDATE SET "
            "password_hash=excluded.password_hash, role=excluded.role, "
            "active=1, updated_at=excluded.updated_at",
            (username, password_hash, role, now, now),
        )
        self.conn.commit()
        user = self.get_user_by_username(username)
        assert user and user.id is not None
        return user.id

    def delete_user(self, user_id: int) -> None:
        self.conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        self.conn.commit()

    def deactivate_users_except(self, usernames: list[str]) -> None:
        placeholders = ",".join("?" for _ in usernames)
        if usernames:
            self.conn.execute(
                f"UPDATE users SET active = 0 WHERE username NOT IN ({placeholders})",
                usernames,
            )
        else:
            self.conn.execute("UPDATE users SET active = 0")
        self.conn.commit()

    def get_user_permissions(self, user_id: int) -> list[str]:
        rows = self.conn.execute(
            "SELECT page FROM user_page_permissions WHERE user_id = ? "
            "ORDER BY page", (user_id,),
        ).fetchall()
        return [str(row["page"]) for row in rows]

    def set_user_permissions(self, user_id: int, pages: list[str]) -> None:
        with self.conn:
            self.conn.execute(
                "DELETE FROM user_page_permissions WHERE user_id = ?", (user_id,)
            )
            self.conn.executemany(
                "INSERT INTO user_page_permissions (user_id, page) VALUES (?, ?)",
                [(user_id, page) for page in pages],
            )

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type: type[BaseException] | None,
                 exc: BaseException | None,
                 tb: Any) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # ShareLink CRUD
    # ------------------------------------------------------------------ #
    def add_share_link(self, link: ShareLink) -> int:
        """添加分享链接，返回新记录的自增 ID。"""
        cur = self.conn.execute(
            """
            INSERT INTO share_links
                (url, surl, password, title, share_id, share_uk,
                 status, created_at, last_checked, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (link.url, link.surl, link.password, link.title, link.share_id,
             link.share_uk, link.status, link.created_at, link.last_checked,
             link.note),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get_share_link(self, link_id: int) -> ShareLink | None:
        """按 ID 获取单个分享链接，不存在返回 None。"""
        cur = self.conn.execute(
            "SELECT * FROM share_links WHERE id = ?", (link_id,)
        )
        row = cur.fetchone()
        return self._row_to_share_link(row) if row else None

    def list_share_links(self) -> list[ShareLink]:
        """列出全部分享链接（按 created_at 升序）。"""
        cur = self.conn.execute(
            "SELECT * FROM share_links ORDER BY created_at ASC"
        )
        return [self._row_to_share_link(r) for r in cur.fetchall()]

    def update_share_link(self, link: ShareLink) -> None:
        """更新分享链接。link.id 不能为 None。"""
        if link.id is None:
            raise ValueError("更新 ShareLink 时 id 不能为 None")
        self.conn.execute(
            """
            UPDATE share_links SET
                url = ?, surl = ?, password = ?, title = ?, share_id = ?,
                share_uk = ?, status = ?, created_at = ?, last_checked = ?,
                note = ?
            WHERE id = ?
            """,
            (link.url, link.surl, link.password, link.title, link.share_id,
             link.share_uk, link.status, link.created_at, link.last_checked,
             link.note, link.id),
        )
        self.conn.commit()

    def delete_share_link(self, link_id: int) -> None:
        """删除分享链接，级联删除其文件条目与同步映射（依赖 FK CASCADE）。"""
        self.conn.execute("DELETE FROM share_links WHERE id = ?", (link_id,))
        self.conn.commit()

    # ------------------------------------------------------------------ #
    # FileEntry CRUD
    # ------------------------------------------------------------------ #
    def add_file_entry(self, entry: FileEntry) -> int:
        """添加文件条目，返回新记录的自增 ID。"""
        cur = self.conn.execute(
            """
            INSERT INTO file_entries
                (share_link_id, fs_id, name, path, is_dir, size, md5,
                 modified_time, local_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (entry.share_link_id, entry.fs_id, entry.name, entry.path,
             int(entry.is_dir), entry.size, entry.md5, entry.modified_time,
             entry.local_path),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def add_file_entries(self, entries: list[FileEntry]) -> int:
        """在一个事务中批量添加文件条目，返回写入数量。"""
        rows = [
            (
                entry.share_link_id, entry.fs_id, entry.name, entry.path,
                int(entry.is_dir), entry.size, entry.md5,
                entry.modified_time, entry.local_path,
            )
            for entry in entries
        ]
        self.conn.executemany(
            """
            INSERT INTO file_entries
                (share_link_id, fs_id, name, path, is_dir, size, md5,
                 modified_time, local_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    def get_file_entries(self, share_link_id: int) -> list[FileEntry]:
        """获取某分享链接下的全部文件条目（按 path 升序）。"""
        cur = self.conn.execute(
            "SELECT * FROM file_entries WHERE share_link_id = ? "
            "ORDER BY path ASC",
            (share_link_id,),
        )
        return [self._row_to_file_entry(r) for r in cur.fetchall()]

    def clear_file_entries(self, share_link_id: int) -> None:
        """清空某分享链接下的全部文件条目。"""
        self.conn.execute(
            "DELETE FROM file_entries WHERE share_link_id = ?",
            (share_link_id,),
        )
        self.conn.commit()

    # ------------------------------------------------------------------ #
    # SyncMapping CRUD
    # ------------------------------------------------------------------ #
    def add_sync_mapping(self, mapping: SyncMapping) -> int:
        """添加同步映射，返回新记录的自增 ID。"""
        cur = self.conn.execute(
            """
            INSERT INTO sync_mappings
                (share_link_id, remote_path, local_path, auto_sync,
                last_synced, sync_strategy, schedule_interval, storage_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (mapping.share_link_id, mapping.remote_path, mapping.local_path,
             int(mapping.auto_sync), mapping.last_synced,
             mapping.sync_strategy, mapping.schedule_interval,
             mapping.storage_type),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get_sync_mappings(self, share_link_id: int) -> list[SyncMapping]:
        """获取某分享链接下的全部同步映射。"""
        cur = self.conn.execute(
            "SELECT * FROM sync_mappings WHERE share_link_id = ? "
            "ORDER BY id ASC",
            (share_link_id,),
        )
        return [self._row_to_sync_mapping(r) for r in cur.fetchall()]

    def get_sync_mapping(self, mapping_id: int) -> SyncMapping | None:
        """按 ID 获取单个同步映射，不存在返回 None。"""
        cur = self.conn.execute(
            "SELECT * FROM sync_mappings WHERE id = ?", (mapping_id,)
        )
        row = cur.fetchone()
        return self._row_to_sync_mapping(row) if row else None

    def update_sync_mapping(self, mapping: SyncMapping) -> None:
        """更新同步映射。mapping.id 不能为 None。"""
        if mapping.id is None:
            raise ValueError("更新 SyncMapping 时 id 不能为 None")
        self.conn.execute(
            """
            UPDATE sync_mappings SET
                share_link_id = ?, remote_path = ?, local_path = ?,
                auto_sync = ?, last_synced = ?, sync_strategy = ?,
                schedule_interval = ?, storage_type = ?
            WHERE id = ?
            """,
            (mapping.share_link_id, mapping.remote_path, mapping.local_path,
             int(mapping.auto_sync), mapping.last_synced,
             mapping.sync_strategy, mapping.schedule_interval,
             mapping.storage_type, mapping.id),
        )
        self.conn.commit()

    def delete_sync_mapping(self, mapping_id: int) -> None:
        """按 ID 删除单个同步映射。"""
        self.conn.execute(
            "DELETE FROM sync_mappings WHERE id = ?", (mapping_id,)
        )
        self.conn.commit()

    def get_all_sync_mappings(self) -> list[SyncMapping]:
        """获取全部同步映射（用于全局同步任务）。"""
        cur = self.conn.execute(
            "SELECT * FROM sync_mappings ORDER BY id ASC"
        )
        return [self._row_to_sync_mapping(r) for r in cur.fetchall()]

    def add_sync_run(self, mapping_id: int | None, trigger_type: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO sync_runs "
            "(mapping_id, trigger_type, status, started_at) "
            "VALUES (?, ?, 'queued', ?)",
            (mapping_id, trigger_type, __import__('time').time()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def update_sync_run(self, run_id: int, status: str, message: str = "") -> None:
        finished_at = __import__('time').time() if status in {"success", "failed"} else 0
        self.conn.execute(
            "UPDATE sync_runs SET status = ?, message = ?, finished_at = ? "
            "WHERE id = ?",
            (status, message, finished_at, run_id),
        )
        self.conn.commit()

    def list_sync_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT r.*, m.remote_path, m.local_path "
            "FROM sync_runs r LEFT JOIN sync_mappings m ON m.id = r.mapping_id "
            "ORDER BY r.id DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in cur.fetchall()]

    # ------------------------------------------------------------------ #
    # 行 → dataclass 转换
    # ------------------------------------------------------------------ #
    @staticmethod
    def _row_to_user(row: sqlite3.Row) -> User:
        return User(
            id=row["id"], username=row["username"],
            password_hash=row["password_hash"], role=row["role"],
            active=bool(row["active"]), created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_share_link(row: sqlite3.Row) -> ShareLink:
        return ShareLink(
            id=row["id"],
            url=row["url"],
            surl=row["surl"],
            password=row["password"],
            title=row["title"],
            share_id=row["share_id"],
            share_uk=row["share_uk"],
            status=row["status"],
            created_at=row["created_at"],
            last_checked=row["last_checked"],
            note=row["note"],
        )

    @staticmethod
    def _row_to_file_entry(row: sqlite3.Row) -> FileEntry:
        return FileEntry(
            id=row["id"],
            share_link_id=row["share_link_id"],
            fs_id=row["fs_id"],
            name=row["name"],
            path=row["path"],
            is_dir=bool(row["is_dir"]),
            size=row["size"],
            md5=row["md5"],
            modified_time=row["modified_time"],
            local_path=row["local_path"],
        )

    @staticmethod
    def _row_to_sync_mapping(row: sqlite3.Row) -> SyncMapping:
        return SyncMapping(
            id=row["id"],
            share_link_id=row["share_link_id"],
            remote_path=row["remote_path"],
            local_path=row["local_path"],
            auto_sync=bool(row["auto_sync"]),
            last_synced=row["last_synced"],
            sync_strategy=row["sync_strategy"],
            schedule_interval=row["schedule_interval"],
            storage_type=row["storage_type"],
        )
