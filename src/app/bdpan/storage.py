"""同步目标路径识别与 SMB 挂载安全检查。"""

from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath


LOCAL = "local"
SMB_MOUNT = "smb_mount"


def validate_storage_path(raw_path: str, storage_type: str) -> str:
    """校验并规范化映射目标；不要求 NAS 当前在线。"""
    value = os.path.expanduser(raw_path.strip())
    if not value:
        raise ValueError("目标路径不能为空")
    if storage_type not in {LOCAL, SMB_MOUNT}:
        raise ValueError("未知的存储类型")

    if os.name == "nt":
        windows_path = PureWindowsPath(value)
        if not windows_path.is_absolute():
            raise ValueError("Windows 目标必须是盘符绝对路径或 SMB UNC 路径")
        if storage_type == SMB_MOUNT and not (
            value.startswith("\\\\") or _windows_remote_drive(value)
        ):
            raise ValueError(
                "SMB 目标请填写 UNC 路径（如 \\\\NAS地址\\共享名\\目录），"
                "或已连接的网络驱动器路径"
            )
        return str(windows_path)

    path = Path(value)
    if not path.is_absolute():
        raise ValueError("Linux/macOS 目标必须是绝对路径")
    if storage_type == SMB_MOUNT and value.startswith("//"):
        raise ValueError("Linux/macOS 请先挂载 SMB，再填写挂载点下的绝对路径")
    return str(path)


def probe_storage(raw_path: str, storage_type: str) -> dict[str, object]:
    """返回目标的连接状态，并避免把失效挂载误判为本地目录。"""
    try:
        normalized = validate_storage_path(raw_path, storage_type)
    except ValueError as exc:
        return {"ok": False, "kind": storage_type, "message": str(exc)}

    path = Path(normalized)
    if storage_type == SMB_MOUNT:
        if os.name == "nt":
            if not (normalized.startswith("\\\\") or _windows_remote_drive(normalized)):
                return {"ok": False, "kind": storage_type, "message": "该路径不是 SMB 网络路径"}
        else:
            mount = _find_mount_ancestor(path)
            if mount is None:
                return {
                    "ok": False,
                    "kind": storage_type,
                    "message": "未检测到 SMB 挂载点；请先挂载 NAS，避免文件误写入系统盘",
                }

    existing = _nearest_existing(path)
    if existing is None:
        return {
            "ok": False, "kind": storage_type,
            "message": "目标及其父目录不可访问；请检查 NAS、挂载和权限",
        }
    if not existing.is_dir():
        return {"ok": False, "kind": storage_type, "message": "目标路径的父级不是目录"}
    if not os.access(existing, os.W_OK):
        return {"ok": False, "kind": storage_type, "message": "目标目录可访问，但当前服务账号没有写权限"}
    return {
        "ok": True,
        "kind": storage_type,
        "message": "SMB 存储已连接且可写" if storage_type == SMB_MOUNT else "本机目录可写",
        "existing_parent": str(existing),
    }


def ensure_storage_ready(raw_path: str, storage_type: str) -> None:
    result = probe_storage(raw_path, storage_type)
    if not result["ok"]:
        prefix = "SMB 存储不可用" if storage_type == SMB_MOUNT else "目标目录不可用"
        raise OSError(f"{prefix}：{result['message']}（{raw_path}）")


def _nearest_existing(path: Path) -> Path | None:
    candidate = path
    while True:
        try:
            if candidate.exists():
                return candidate
        except OSError:
            return None
        if candidate.parent == candidate:
            return None
        candidate = candidate.parent


def _find_mount_ancestor(path: Path) -> Path | None:
    candidate = _nearest_existing(path)
    while candidate is not None and candidate.parent != candidate:
        try:
            if candidate.is_mount():
                return candidate
        except OSError:
            return None
        candidate = candidate.parent
    return None


def _windows_remote_drive(value: str) -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes
        root = PureWindowsPath(value).anchor
        return bool(root) and ctypes.windll.kernel32.GetDriveTypeW(root) == 4
    except (AttributeError, OSError):
        return False
