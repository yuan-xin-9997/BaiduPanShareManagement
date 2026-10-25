"""网盘识别与客户端选择；保留原百度客户端接口。"""
import re
from urllib.parse import parse_qs, urlparse

from .client import BaiduPanClient, BaiduPanError, normalize_link

PROVIDERS = {"baidu": "百度网盘", "quark": "夸克网盘"}


def normalize_share(raw: str) -> tuple[str, str, str]:
    if not isinstance(raw, str):
        raise ValueError("分享链接必须是文本")
    match = re.search(r"https?://[^\s<>\"，。]+", raw)
    if not match:
        raise ValueError("无法解析分享链接")
    parsed = urlparse(match.group())
    if parsed.hostname == "pan.baidu.com":
        url, password = normalize_link(raw)
        return "baidu", url, password
    if parsed.hostname == "pan.quark.cn" and re.fullmatch(r"/s/[A-Za-z0-9_-]+/?", parsed.path):
        query = parse_qs(parsed.query)
        password = query.get("pwd", query.get("passcode", [""]))[0]
        code = re.search(r"提取码[：:]\s*([A-Za-z0-9]{4})", raw)
        return "quark", f"https://pan.quark.cn{parsed.path.rstrip('/')}", password or (code.group(1) if code else "")
    raise ValueError("暂不支持此网盘链接，目前支持百度网盘、夸克网盘")


def create_client(provider: str, cookie: str):
    if provider == "baidu":
        return BaiduPanClient(cookie=cookie)
    if provider == "quark":
        from .quark import QuarkPanClient
        return QuarkPanClient(cookie=cookie)
    raise ValueError("不支持的网盘类型")
