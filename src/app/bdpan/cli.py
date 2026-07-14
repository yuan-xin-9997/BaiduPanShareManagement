#!/usr/bin/env python3
"""BaiduPanShareManagement - 百度网盘分享链接管理工具。

用法:
    python -m bdpan.cli <command> [options]

命令:
    add <url> [password]  添加分享链接
    list                  列出所有分享链接
    tree <link_id>        查看分享链接的目录结构
    refresh <link_id>     刷新分享链接的文件列表
    remove <link_id>      删除分享链接
    sync <mapping_id>     执行同步
    sync-all              同步所有映射
    mapping <link_id> <remote_path> <local_path>  创建同步映射
    mappings              列出所有同步映射
    config                查看当前配置
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from .models import FileEntry, ShareLink, SyncMapping
from .database import Database
from .sync import SyncManager, SyncStrategy
from .config import load_app_config

logger = logging.getLogger("bdpan.cli")

def get_db() -> Database:
    """获取数据库实例。"""
    config = load_app_config()
    config.database_path.parent.mkdir(parents=True, exist_ok=True)
    return Database(str(config.database_path))


def get_cookie() -> str:
    """获取百度网盘 Cookie。"""
    config = load_app_config()
    try:
        secrets_config = json.loads(config.secret_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        secrets_config = {}
    cookie = secrets_config.get("cookie", "") or os.environ.get("BDPAN_COOKIE", "")
    if not cookie:
        print("错误：未配置百度网盘 Cookie。")
        print("请通过以下方式设置：")
        print("  1. 环境变量: export BDPAN_COOKIE='你的Cookie'")
        print("  2. 配置文件: bdpan config --cookie '你的Cookie'")
        sys.exit(1)
    return cookie


# ------------------------------------------------------------------
# 命令实现
# ------------------------------------------------------------------

def cmd_add(args: argparse.Namespace) -> None:
    """添加分享链接。"""
    from .client import BaiduPanClient

    url = args.url
    password = args.password or ""

    # 尝试解析链接
    cookie = get_cookie()
    client = BaiduPanClient(cookie=cookie)

    print(f"正在解析分享链接: {url}")
    try:
        share_info = client.parse_share(url, password)
    except Exception as e:
        print(f"解析失败: {e}")
        sys.exit(1)

    db = get_db()
    link = ShareLink(
        id=None,
        url=url,
        surl=share_info.surl,
        password=password,
        title=share_info.title,
        share_id=share_info.share_id,
        share_uk=share_info.share_uk,
        status="active",
        created_at=time.time(),
        last_checked=time.time(),
        note=args.note or "",
    )
    link_id = db.add_share_link(link)

    # 保存文件条目
    db.add_file_entries([
        FileEntry(
            id=None,
            share_link_id=link_id,
            fs_id=f.fs_id,
            name=f.name,
            path=f.path,
            is_dir=f.is_dir,
            size=f.size,
            md5=f.md5,
            modified_time=f.modified_time,
            local_path=None,
        )
        for f in share_info.files
    ])

    print(f"✅ 已添加分享链接 (ID: {link_id})")
    print(f"   标题: {share_info.title}")
    print(f"   文件数: {len(share_info.files)}")


def cmd_list(args: argparse.Namespace) -> None:
    """列出所有分享链接。"""
    db = get_db()
    links = db.list_share_links()
    if not links:
        print("暂无分享链接。使用 'bdpan add <url>' 添加。")
        return

    print(f"{'ID':<5} {'状态':<8} {'标题':<40} {'文件数':<8} {'添加时间':<20}")
    print("-" * 85)
    for link in links:
        entries = db.get_file_entries(link.id)
        t = time.strftime("%Y-%m-%d %H:%M", time.localtime(link.created_at))
        title = link.title[:38] + ".." if len(link.title) > 40 else link.title
        print(f"{link.id:<5} {link.status:<8} {title:<40} {len(entries):<8} {t:<20}")


def cmd_tree(args: argparse.Namespace) -> None:
    """查看目录结构。"""
    db = get_db()
    link = db.get_share_link(args.link_id)
    if not link:
        print(f"未找到 ID 为 {args.link_id} 的分享链接")
        sys.exit(1)

    entries = db.get_file_entries(args.link_id)
    if not entries:
        print("该链接没有文件条目，可能需要刷新。使用 'bdpan refresh <link_id>'")
        return

    print(f"📂 {link.title}")
    _print_tree(entries, prefix="")


def _print_tree(entries: list[FileEntry], parent: str = "", prefix: str = "") -> None:
    """递归打印目录树。"""
    children = [e for e in entries if _parent_of(e.path) == parent]
    children.sort(key=lambda e: (not e.is_dir, e.name))

    for i, entry in enumerate(children):
        is_last = i == len(children) - 1
        connector = "└── " if is_last else "├── "
        icon = "📁" if entry.is_dir else "📄"
        size_str = "" if entry.is_dir else f" ({_format_size(entry.size)})"
        print(f"{prefix}{connector}{icon} {entry.name}{size_str}")

        if entry.is_dir:
            new_prefix = prefix + ("    " if is_last else "│   ")
            _print_tree(entries, entry.path, new_prefix)


def _parent_of(path: str) -> str:
    """获取路径的父目录路径。"""
    idx = path.rfind("/")
    return path[:idx] if idx >= 0 else ""


def _format_size(size: int) -> str:
    """格式化文件大小。"""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}PB"


def cmd_refresh(args: argparse.Namespace) -> None:
    """刷新分享链接的文件列表。"""
    from .client import BaiduPanClient

    db = get_db()
    link = db.get_share_link(args.link_id)
    if not link:
        print(f"未找到 ID 为 {args.link_id} 的分享链接")
        sys.exit(1)

    cookie = get_cookie()
    client = BaiduPanClient(cookie=cookie)

    print(f"正在刷新: {link.title}")
    try:
        share_info = client.parse_share(link.url, link.password)
    except Exception as e:
        print(f"刷新失败: {e}")
        link.status = "invalid"
        link.last_checked = time.time()
        db.update_share_link(link)
        sys.exit(1)

    # 清空旧条目，写入新条目
    db.clear_file_entries(link.id)
    db.add_file_entries([
        FileEntry(
            id=None,
            share_link_id=link.id,
            fs_id=f.fs_id,
            name=f.name,
            path=f.path,
            is_dir=f.is_dir,
            size=f.size,
            md5=f.md5,
            modified_time=f.modified_time,
            local_path=None,
        )
        for f in share_info.files
    ])

    link.title = share_info.title
    link.status = "active"
    link.last_checked = time.time()
    db.update_share_link(link)

    print(f"✅ 已刷新，共 {len(share_info.files)} 个文件/目录")


def cmd_remove(args: argparse.Namespace) -> None:
    """删除分享链接。"""
    db = get_db()
    link = db.get_share_link(args.link_id)
    if not link:
        print(f"未找到 ID 为 {args.link_id} 的分享链接")
        sys.exit(1)
    db.delete_share_link(args.link_id)
    print(f"✅ 已删除: {link.title}")


def cmd_mapping(args: argparse.Namespace) -> None:
    """创建同步映射。"""
    db = get_db()
    link = db.get_share_link(args.link_id)
    if not link:
        print(f"未找到 ID 为 {args.link_id} 的分享链接")
        sys.exit(1)

    local = os.path.expanduser(args.local_path)
    mapping = SyncMapping(
        id=None,
        share_link_id=args.link_id,
        remote_path=args.remote_path,
        local_path=local,
        auto_sync=args.auto_sync,
        last_synced=0,
        sync_strategy=args.strategy,
    )
    mid = db.add_sync_mapping(mapping)
    print(f"✅ 已创建同步映射 (ID: {mid})")
    print(f"   远程: {args.remote_path}")
    print(f"   本地: {local}")
    print(f"   策略: {args.strategy}")


def cmd_mappings(args: argparse.Namespace) -> None:
    """列出所有同步映射。"""
    db = get_db()
    all_mappings = db.get_all_sync_mappings()
    if not all_mappings:
        print("暂无同步映射。")
        return

    print(f"{'ID':<5} {'链接ID':<7} {'远程路径':<30} {'本地路径':<40} {'策略':<10} {'自动':<5}")
    print("-" * 100)
    for m in all_mappings:
        link = db.get_share_link(m.share_link_id)
        title = link.title[:20] if link else "未知"
        print(f"{m.id:<5} {m.share_link_id:<7} {m.remote_path:<30} {m.local_path:<40} {m.sync_strategy:<10} {'是' if m.auto_sync else '否':<5}")


def cmd_sync(args: argparse.Namespace) -> None:
    """执行单条同步。"""
    db = get_db()
    mapping = db.get_sync_mapping(args.mapping_id)
    if not mapping:
        print(f"未找到 ID 为 {args.mapping_id} 的同步映射")
        sys.exit(1)

    entries = db.get_file_entries(mapping.share_link_id)
    if not entries:
        print("该映射关联的分享链接没有文件条目，请先刷新。")
        sys.exit(1)

    manager = SyncManager(db)

    # 预览
    preview = manager.preview_changes(mapping, entries)
    if preview["add"] or preview["update"] or preview["delete"]:
        print("即将执行以下变更:")
        if preview["add"]:
            print(f"  新增 {len(preview['add'])} 个文件")
        if preview["update"]:
            print(f"  更新 {len(preview['update'])} 个文件")
        if preview["delete"]:
            print(f"  删除 {len(preview['delete'])} 个文件")

        if not args.yes:
            resp = input("确认执行? (y/N) ")
            if resp.lower() != "y":
                print("已取消")
                return

    from .client import BaiduPanClient
    link = db.get_share_link(mapping.share_link_id)
    if not link:
        print("映射关联的分享链接不存在")
        sys.exit(1)
    client = BaiduPanClient(cookie=get_cookie())
    try:
        client.prepare_share_download(link.url, link.password)
        manager = SyncManager(db, client.download_share_file)
        result = manager.sync_mapping(mapping, entries)
    finally:
        client.close()
    print(f"✅ 同步完成: {result.summary()}")
    if result.errors:
        for err in result.errors:
            print(f"  ❌ {err}")


def cmd_sync_all(args: argparse.Namespace) -> None:
    """同步所有映射。"""
    db = get_db()
    mappings = db.get_all_sync_mappings()
    if not mappings:
        print("暂无同步映射。")
        return
    for mapping in mappings:
        entries = db.get_file_entries(mapping.share_link_id)
        link = db.get_share_link(mapping.share_link_id)
        if not link:
            print(f"  [{mapping.id}] 分享链接不存在，已跳过")
            continue
        from .client import BaiduPanClient
        client = BaiduPanClient(cookie=get_cookie())
        try:
            client.prepare_share_download(link.url, link.password)
            manager = SyncManager(db, client.download_share_file)
            result = manager.sync_mapping(mapping, entries)
            print(f"  [{result.mapping_id}] {result.remote_path} -> {result.local_path}: {result.summary()}")
        finally:
            client.close()


def cmd_config(args: argparse.Namespace) -> None:
    """查看或设置配置。"""
    config = load_app_config()
    try:
        secrets_config = json.loads(config.secret_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        secrets_config = {}
    if args.cookie:
        secrets_config["cookie"] = args.cookie
        config.secret_file.parent.mkdir(parents=True, exist_ok=True)
        config.secret_file.write_text(
            json.dumps(secrets_config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("✅ Cookie 已保存")
        return

    print("当前配置:")
    for k, v in config.public_dict().items():
        print(f"  {k}: {v}")
    print(f"  cookie: {'已配置' if secrets_config.get('cookie') else '未配置'}")


# ------------------------------------------------------------------
# 主入口
# ------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bdpan",
        description="百度网盘分享链接管理工具",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # add
    p = sub.add_parser("add", help="添加分享链接")
    p.add_argument("url", help="分享链接 URL")
    p.add_argument("password", nargs="?", default="", help="提取码")
    p.add_argument("--note", default="", help="备注")
    p.set_defaults(func=cmd_add)

    # list
    p = sub.add_parser("list", help="列出所有分享链接")
    p.set_defaults(func=cmd_list)

    # tree
    p = sub.add_parser("tree", help="查看分享链接的目录结构")
    p.add_argument("link_id", type=int, help="分享链接 ID")
    p.set_defaults(func=cmd_tree)

    # refresh
    p = sub.add_parser("refresh", help="刷新分享链接的文件列表")
    p.add_argument("link_id", type=int, help="分享链接 ID")
    p.set_defaults(func=cmd_refresh)

    # remove
    p = sub.add_parser("remove", help="删除分享链接")
    p.add_argument("link_id", type=int, help="分享链接 ID")
    p.set_defaults(func=cmd_remove)

    # mapping
    p = sub.add_parser("mapping", help="创建同步映射")
    p.add_argument("link_id", type=int, help="分享链接 ID")
    p.add_argument("remote_path", help="分享中的远程目录路径")
    p.add_argument("local_path", help="本地文件夹路径")
    p.add_argument("--strategy", default="copy_new", choices=["mirror", "copy_new", "ask"])
    p.add_argument("--auto-sync", action="store_true", help="启用自动同步")
    p.set_defaults(func=cmd_mapping)

    # mappings
    p = sub.add_parser("mappings", help="列出所有同步映射")
    p.set_defaults(func=cmd_mappings)

    # sync
    p = sub.add_parser("sync", help="执行同步")
    p.add_argument("mapping_id", type=int, help="同步映射 ID")
    p.add_argument("-y", "--yes", action="store_true", help="跳过确认")
    p.set_defaults(func=cmd_sync)

    # sync-all
    p = sub.add_parser("sync-all", help="同步所有映射")
    p.set_defaults(func=cmd_sync_all)

    # config
    p = sub.add_parser("config", help="查看或设置配置")
    p.add_argument("--cookie", help="设置百度网盘 Cookie")
    p.set_defaults(func=cmd_config)

    return parser


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
