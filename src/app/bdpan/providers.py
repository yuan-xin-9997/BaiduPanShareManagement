"""网盘识别与客户端选择；保留原百度客户端接口。"""
import re
from urllib.parse import parse_qs, urlparse

from .client import BaiduPanClient, BaiduPanError, normalize_link

PROVIDERS = {"baidu": "百度网盘", "quark": "夸克网盘"}

# 只有网盘明确表示“分享不存在/已删除/已取消/已过期”时才允许把链接标记为
# invalid。提取码错误、限流、风控、网络异常以及无法识别的错误码都保持原状态，
# 避免误杀仍然有效的分享。
QUARK_INVALID_CODES = {41004, 41006, 41011, 41012, 41019}
QUARK_BLOCKED_CODES = {41010, 41031}
BAIDU_INVALID_CODES = {-21}


def is_share_invalid_error(exc: BaseException) -> bool:
    """判断异常是否足以确认分享链接已经失效。"""
    seen: set[int] = set()
    error: BaseException | None = exc
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, BaiduPanError):
            if getattr(error, "share_invalid", False):
                return True
            quark_code = getattr(error, "quark_code", None)
            if quark_code in QUARK_INVALID_CODES | QUARK_BLOCKED_CODES:
                return True
            if error.code in BAIDU_INVALID_CODES:
                return True
        error = error.__cause__ or error.__context__
    return False


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
