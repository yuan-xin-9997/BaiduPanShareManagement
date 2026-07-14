"""数据模型定义。

使用 dataclass 定义三个核心实体：
- ShareLink: 分享链接记录
- FileEntry: 分享链接中的文件条目
- SyncMapping: 分享链接目录与本地文件夹的同步映射
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class User:
    id: int | None
    username: str
    password_hash: str
    role: str
    active: bool
    created_at: float
    updated_at: float


@dataclass
class ShareLink:
    """分享链接记录"""

    id: int | None          # 自增主键
    url: str                # 原始分享链接
    surl: str               # 短链接ID
    password: str           # 提取码（可能为空）
    title: str              # 分享标题
    share_id: int           # 百度分享ID
    share_uk: int           # 分享者UK
    status: str             # 状态: active/expired/invalid
    created_at: float       # 添加时间
    last_checked: float     # 最后检查时间
    note: str               # 用户备注


@dataclass
class FileEntry:
    """分享链接中的文件条目"""

    id: int | None
    share_link_id: int      # 关联的 ShareLink ID
    fs_id: int              # 百度文件ID
    name: str               # 文件名
    path: str               # 在分享中的完整路径
    is_dir: bool            # 是否目录
    size: int               # 文件大小
    md5: str                # MD5
    modified_time: int      # 修改时间戳
    local_path: str | None  # 关联的本地路径（如果已同步）


@dataclass
class SyncMapping:
    """分享链接目录与本地文件夹的同步映射"""

    id: int | None
    share_link_id: int      # 关联的 ShareLink ID
    remote_path: str        # 分享中的远程目录路径
    local_path: str         # 本地文件夹路径
    auto_sync: bool         # 是否自动同步
    last_synced: float      # 最后同步时间
    sync_strategy: str      # 同步策略: mirror/copy_new/ask
    schedule_interval: int = 60  # 自动同步间隔（分钟）
    storage_type: str = "local"  # local / smb_mount
