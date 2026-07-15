"""百度网盘分享链接 API 客户端。

通过百度网盘 Web 接口（Cookie 认证）实现分享链接的解析、
提取码验证、文件列表获取和目录结构递归遍历。
"""

from __future__ import annotations

import logging
import json
import posixpath
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse, parse_qs, unquote

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://pan.baidu.com"
PAN_WEB_APP_ID = "250528"

HEADERS = {
    "Host": "pan.baidu.com",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Sec-Fetch-Site": "same-site",
    "Sec-Fetch-Mode": "navigate",
    "Referer": "https://pan.baidu.com",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

ERROR_CODES: dict[int, str] = {
    -1: "链接错误，链接失效或缺少提取码",
    -4: "无效登录，请退出账号在其他地方的登录",
    -6: "请用浏览器无痕模式获取 Cookie 后再试",
    -7: "转存文件夹名有非法字符",
    -8: "目录中已有同名文件或文件夹存在",
    -9: "提取码错误",
    -12: "提取码错误",
    -62: "链接访问次数过多，请稍后再试",
    0: "成功",
    2: "目标目录不存在",
    4: "目录中存在同名文件",
    12: "转存文件数超过限制",
    20: "容量不足",
    105: "所访问的页面不存在",
    404: "秒传无效",
}

# 预编译正则
_RE_SHAREID = re.compile(r'["\']?shareid["\']?\s*:\s*["\']?(\d+)')
_RE_SHARE_UK = re.compile(r'["\']?share_uk["\']?\s*:\s*["\']?(\d+)')
_RE_FS_ID = re.compile(r'"fs_id":(\d+?),"')
_RE_FILENAME = re.compile(r'"server_filename":"(.+?)","')
_RE_ISDIR = re.compile(r'"isdir":(\d+?),"')


# ------------------------------------------------------------------
# 数据结构
# ------------------------------------------------------------------


@dataclass
class ShareFile:
    """分享链接中的单个文件/目录。"""

    fs_id: int
    name: str
    is_dir: bool
    size: int
    md5: str
    path: str
    created_time: int
    modified_time: int


@dataclass
class ShareInfo:
    """分享链接完整信息。"""

    share_id: int
    share_uk: int
    surl: str
    title: str
    files: list[ShareFile] = field(default_factory=list)
    cookie: str = ""


class BaiduPanError(Exception):
    """百度网盘 API 错误。"""

    def __init__(self, code: int, message: str = "") -> None:
        self.code = code
        self.message = message or ERROR_CODES.get(code, f"未知错误 (code={code})")
        super().__init__(self.message)


# ------------------------------------------------------------------
# 链接解析工具
# ------------------------------------------------------------------


def normalize_link(raw: str) -> tuple[str, str]:
    """将各种格式的分享链接+提取码文本规范化。

    支持格式:
      - https://pan.baidu.com/s/1xxx
      - https://pan.baidu.com/s/1xxx?pwd=1234
      - https://pan.baidu.com/s/1xxx 提取码: 1234
      - 链接: https://pan.baidu.com/s/1xxx 提取码: xxxx
      - https://pan.baidu.com/share/init?surl=7Mxxx&pwd=1234

    Returns:
        (url, password) 元组
    """
    text = raw.strip()

    # 提取 URL
    url_match = re.search(r"https?://pan\.baidu\.com/\S+", text)
    if not url_match:
        raise BaiduPanError(-1, "无法从输入中解析出百度网盘链接")
    url = url_match.group(0)

    # 先从原始 URL 参数中提取 pwd（规范化前提取，避免丢参数）
    password = ""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "pwd" in qs:
        password = qs["pwd"][0]

    # 规范化 share/init?surl= 格式 -> /s/1xxx
    if "share/init" in url and "surl=" in url:
        surl_match = re.search(r"surl=([A-Za-z0-9_\-]+)", url)
        if surl_match:
            url = f"{BASE_URL}/s/1{surl_match.group(1)}"
    else:
        # 去掉普通 URL 中的 pwd 参数
        url = url.split("?")[0] if "?" in url else url

    # 如果 URL 中没有 pwd，从文本中提取提取码
    if not password:
        # 匹配 "提取码: xxxx" 或 "提取码：xxxx"
        pwd_match = re.search(r"提取码[：:]\s*([A-Za-z0-9]{4})", text)
        if pwd_match:
            password = pwd_match.group(1)
        else:
            # 匹配 URL 后空格加 4 位提取码
            parts = text.split()
            for part in parts[1:]:
                if re.match(r"^[A-Za-z0-9]{4}$", part):
                    password = part
                    break

    return url, password


def extract_surl(url: str) -> str:
    """从规范化的分享链接中提取 surl。

    https://pan.baidu.com/s/1tU58ChMSPmx4e3-kDx1mLg -> 1tU58ChMSPmx4e3-kDx1mLg
    """
    match = re.search(r"/s/([A-Za-z0-9_\-]+)", url)
    if not match:
        raise BaiduPanError(-1, f"无法从链接中提取 surl: {url}")
    return match.group(1)


def update_cookie_bdclnd(cookie: str, bdclnd: str) -> str:
    """更新 cookie 中的 BDCLND 值。"""
    parts = [p.strip() for p in cookie.split(";") if p.strip()]
    result = []
    found = False
    for part in parts:
        if part.startswith("BDCLND="):
            result.append(f"BDCLND={bdclnd}")
            found = True
        else:
            result.append(part)
    if not found:
        result.append(f"BDCLND={bdclnd}")
    return "; ".join(result)


# ------------------------------------------------------------------
# 核心客户端
# ------------------------------------------------------------------


class BaiduPanClient:
    """百度网盘分享链接客户端。

    使用 Web 接口 + Cookie 认证，无需开放平台 API Key。

    Args:
        cookie: 百度网盘 Cookie（从浏览器获取）
        proxy: 代理地址，如 "http://127.0.0.1:7890"
        timeout: 请求超时秒数
    """

    def __init__(
        self,
        cookie: str,
        proxy: str | None = None,
        timeout: int = 15,
    ) -> None:
        self.cookie = cookie
        self.timeout = timeout
        self.session = requests.Session()
        self.download_session = requests.Session()
        # 大文件流不继承 HTTP_PROXY/HTTPS_PROXY；本机代理长时间传输时
        # 容易主动断开 d.pcs.baidu.com 连接。
        self.download_session.trust_env = False
        self.session.headers.update(HEADERS)
        self.session.headers["Cookie"] = cookie
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}
        self.session.verify = False
        self.bdstoken: str = ""
        self._download_share_id: int = 0
        self._download_share_uk: int = 0
        self._download_sekey: str = ""
        self._download_surl: str = ""
        self._suppress_warnings()

    @staticmethod
    def _suppress_warnings() -> None:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # ------------------------------------------------------------------
    # 内部请求方法
    # ------------------------------------------------------------------

    def _get(self, url: str, params: dict | None = None, extra_headers: dict | None = None) -> dict:
        """GET 请求，返回 JSON。"""
        headers = dict(self.session.headers)
        if extra_headers:
            headers.update(extra_headers)
        for attempt in range(3):
            try:
                r = self.session.get(
                    url, params=params, headers=headers,
                    timeout=self.timeout, allow_redirects=True, verify=False,
                )
                return r.json()
            except requests.exceptions.JSONDecodeError:
                # 返回的不是 JSON，可能是 HTML 页面
                raise BaiduPanError(-1, "服务器返回非 JSON 响应，可能需要重新登录")
            except requests.exceptions.RequestException as e:
                if attempt == 2:
                    raise BaiduPanError(-1, f"网络请求失败: {e}")
                time.sleep(1)
        raise BaiduPanError(-1, "请求重试次数耗尽")

    def _post(self, url: str, params: dict | None = None, data: dict | None = None) -> dict:
        """POST 请求，返回 JSON。"""
        for attempt in range(3):
            try:
                r = self.session.post(
                    url, params=params, data=data,
                    headers=self.session.headers,
                    timeout=self.timeout, allow_redirects=False, verify=False,
                )
                return r.json()
            except requests.exceptions.JSONDecodeError:
                raise BaiduPanError(-1, "服务器返回非 JSON 响应")
            except requests.exceptions.RequestException as e:
                if attempt == 2:
                    raise BaiduPanError(-1, f"网络请求失败: {e}")
                time.sleep(1)
        raise BaiduPanError(-1, "请求重试次数耗尽")

    def _get_html(self, url: str) -> str:
        """GET 请求，返回 HTML 文本。"""
        for attempt in range(3):
            try:
                r = self.session.get(
                    url, headers=self.session.headers,
                    timeout=self.timeout, verify=False,
                )
                return r.content.decode("utf-8", errors="replace")
            except requests.exceptions.RequestException as e:
                if attempt == 2:
                    raise BaiduPanError(-1, f"网络请求失败: {e}")
                time.sleep(1)
        raise BaiduPanError(-1, "请求重试次数耗尽")

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def get_bdstoken(self) -> str:
        """获取 bdstoken，后续操作的先决条件。"""
        if self.bdstoken:
            return self.bdstoken

        url = f"{BASE_URL}/api/gettemplatevariable"
        params = {
            "clienttype": "0",
            "app_id": PAN_WEB_APP_ID,
            "web": "1",
            "fields": '["bdstoken","token","uk","isdocuser","servertime"]',
        }
        data = self._get(url, params)
        errno = data.get("errno", -1)
        if errno != 0:
            detail = ERROR_CODES.get(errno, "百度拒绝了当前登录凭据")
            raise BaiduPanError(
                errno,
                f"获取 bdstoken 失败（errno={errno}）：{detail}。"
                "请重新登录 pan.baidu.com 后复制完整 Cookie",
            )
        result = data.get("result")
        token = result.get("bdstoken") if isinstance(result, dict) else None
        if not token:
            raise BaiduPanError(
                -1,
                "获取 bdstoken 失败：百度响应中没有 bdstoken。"
                "请确认 Cookie 包含有效的 BDUSS，并重新登录后复制完整 Cookie",
            )
        self.bdstoken = str(token)
        logger.info("获取 bdstoken 成功")
        return self.bdstoken

    def verify_password(self, surl: str, password: str) -> str:
        """验证提取码，返回 randsk (bdclnd)。

        Args:
            surl: 短链接 ID
            password: 提取码

        Returns:
            randsk 字符串，用于更新 cookie
        """
        bdstoken = self.get_bdstoken()
        # /s/1xxx 中的前导 "1" 是分享页路径标记，/share/verify
        # 接口需要的是不含该标记的真实 surl，否则会返回 errno 105。
        api_surl = surl[1:] if surl.startswith("1") else surl
        url = f"{BASE_URL}/share/verify"
        params = {
            "surl": api_surl,
            "bdstoken": bdstoken,
            "t": str(int(time.time() * 1000)),
            "channel": "chunlei",
            "web": "1",
            "clienttype": "0",
        }
        data_payload = {"pwd": password, "vcode": "", "vcode_str": ""}
        result = self._post(url, params=params, data=data_payload)

        errno = result.get("errno", -1)
        if errno != 0:
            raise BaiduPanError(errno)

        randsk = result.get("randsk", "")
        if not randsk:
            raise BaiduPanError(-1, "验证成功但未返回 randsk")
        logger.info("提取码验证成功，获得 randsk")
        return randsk

    def _parse_share_page(self, share_url: str) -> dict[str, Any]:
        """访问分享页面，解析 HTML 提取关键参数。

        Returns:
            {
                "shareid": str,
                "share_uk": str,
                "fs_ids": list[str],
                "filenames": list[str],
                "isdirs": list[str],
            }
        """
        html = self._get_html(share_url)

        share_ids = _RE_SHAREID.findall(html)
        share_uks = _RE_SHARE_UK.findall(html)
        fs_ids = _RE_FS_ID.findall(html)
        filenames = _RE_FILENAME.findall(html)
        isdirs = _RE_ISDIR.findall(html)

        if not all([share_ids, share_uks, fs_ids, filenames, isdirs]):
            raise BaiduPanError(-1, "解析分享页面失败，可能链接已失效")

        # 提取标题
        title_match = re.search(r'"share_title":"(.+?)"', html)
        title = title_match.group(1) if title_match else filenames[0]

        # 去重 filenames 和 isdirs（保持顺序）
        unique_names = list(dict.fromkeys(filenames))
        unique_isdirs = isdirs[: len(unique_names)] if len(isdirs) >= len(unique_names) else isdirs

        return {
            "shareid": share_ids[0],
            "share_uk": share_uks[0],
            "fs_ids": fs_ids,
            "filenames": unique_names,
            "isdirs": unique_isdirs,
            "title": title,
        }

    def get_share_file_list(
        self,
        share_id: int,
        share_uk: int,
        dir_path: str = "",
        root: bool = True,
    ) -> list[dict[str, Any]]:
        """通过 share/list 接口获取文件列表。

        Args:
            share_id: 分享 ID
            share_uk: 分享者 UK
            dir_path: 子目录路径（根目录传空字符串）
            root: 是否请求根目录

        Returns:
            文件信息字典列表
        """
        bdstoken = self.get_bdstoken()
        url = f"{BASE_URL}/share/list"
        base_params: dict[str, Any] = {
            "shareid": share_id,
            "uk": share_uk,
            "bdstoken": bdstoken,
            "channel": "chunlei",
            "web": "1",
            "clienttype": "0",
            # 默认按名称排序在大目录的部分 offset 会返回 errno 115。
            # 时间排序可稳定遍历同一目录的全部分页。
            "order": "time",
            "desc": "0",
        }
        if root:
            base_params["root"] = "1"
        else:
            base_params["dir"] = dir_path

        # 百度目前每页最多返回 100 条，即使 num 传得更大也会截断。
        # 必须逐页读取，否则镜像同步会把未进入索引的远端文件误判为已删除。
        page_size = 100
        page = 1
        result: list[dict[str, Any]] = []
        while True:
            params = {**base_params, "page": page, "num": page_size}
            data = self._get(url, params)
            errno = data.get("errno", -1)
            recovered_page = errno != 0
            if errno != 0:
                # 部分特殊条目会让百度把整页响应为 errno 115。
                # 对失败页降级为逐条读取，既不漏掉整页，也避免用残缺索引同步。
                items = []
                start = (page - 1) * page_size
                for offset in range(start, start + page_size):
                    single_params = {
                        **base_params,
                        "page": offset + 1,
                        "num": 1,
                    }
                    single_data = self._get(url, single_params)
                    single_errno = single_data.get("errno", -1)
                    if single_errno == 115:
                        logger.warning(
                            "百度拒绝列出目录中的第 %d 个特殊条目，已跳过",
                            offset + 1,
                        )
                        continue
                    if single_errno != 0:
                        raise BaiduPanError(
                            single_errno,
                            f"读取分享目录第 {offset + 1} 个条目失败 "
                            f"(errno={single_errno})",
                        )
                    items.extend(single_data.get("list", []))
            else:
                items = data.get("list", [])
            result.extend(items)
            # 降级页可能因跳过 errno 115 条目而不足 100 条，但后面仍有分页。
            if not recovered_page and len(items) < page_size:
                break
            page += 1

        return result

    def parse_share(
        self,
        raw_url: str,
        password: str = "",
        recursive: bool = True,
        max_depth: int = 20,
    ) -> ShareInfo:
        """完整解析分享链接。

        这是主入口方法，完成：链接规范化 → 提取码验证 →
        页面解析 → 文件列表获取（可选递归）。

        Args:
            raw_url: 原始分享链接文本（可包含提取码）
            password: 提取码（如果 raw_url 中已包含则可省略）
            recursive: 是否递归获取子目录文件
            max_depth: 递归最大深度

        Returns:
            ShareInfo 完整分享信息
        """
        # 1. 规范化链接
        url, pwd_from_url = normalize_link(raw_url)
        pwd = password or pwd_from_url
        surl = extract_surl(url)
        logger.info("解析分享链接: surl=%s, pwd=%s", surl, "***" if pwd else "(空)")

        # 2. 验证提取码（如果有）
        if pwd:
            randsk = self.verify_password(surl, pwd)
            # 更新 cookie
            self.cookie = update_cookie_bdclnd(self.cookie, randsk)
            self.session.headers["Cookie"] = self.cookie

        # 3. 解析分享页面
        parsed = self._parse_share_page(url)
        share_id = int(parsed["shareid"])
        share_uk = int(parsed["share_uk"])
        title = parsed["title"]

        # 4. 尝试用 share/list API 获取详细文件列表
        files: list[ShareFile] = []
        api_list = self.get_share_file_list(share_id, share_uk, root=True)

        if api_list:
            # API 返回了详细列表
            root_files: list[ShareFile] = []
            for item in api_list:
                sf = self._api_item_to_share_file(item)
                files.append(sf)
                root_files.append(sf)

                # 递归获取子目录
                if recursive and sf.is_dir and max_depth > 1:
                    sub_files = self._recursive_list(
                        share_id, share_uk, sf.path, max_depth - 1
                    )
                    files.extend(sub_files)
            self._normalize_share_paths(files, root_files)
        else:
            # 回退到 HTML 解析结果
            fs_ids = parsed["fs_ids"]
            names = parsed["filenames"]
            isdirs = parsed["isdirs"]
            for i, fs_id in enumerate(fs_ids):
                name = names[i] if i < len(names) else f"file_{i}"
                is_dir = isdirs[i] == "1" if i < len(isdirs) else False
                files.append(ShareFile(
                    fs_id=int(fs_id),
                    name=name,
                    is_dir=is_dir,
                    size=0,
                    md5="",
                    path=f"/{name}",
                    created_time=0,
                    modified_time=0,
                ))

        logger.info("解析完成: %s, 共 %d 个文件/目录", title, len(files))

        return ShareInfo(
            share_id=share_id,
            share_uk=share_uk,
            surl=surl,
            title=title,
            files=files,
            cookie=self.cookie,
        )

    def prepare_share_download(self, raw_url: str, password: str = "") -> None:
        """验证分享并准备后续文件下载，不遍历目录。"""
        url, pwd_from_url = normalize_link(raw_url)
        pwd = password or pwd_from_url
        surl = extract_surl(url)
        sekey = ""
        if pwd:
            sekey = self.verify_password(surl, pwd)
            self.cookie = update_cookie_bdclnd(self.cookie, sekey)
            self.session.headers["Cookie"] = self.cookie
        parsed = self._parse_share_page(url)
        self._download_share_id = int(parsed["shareid"])
        self._download_share_uk = int(parsed["share_uk"])
        self._download_sekey = unquote(sekey)
        self._download_surl = surl
        bduss = self._get_cookie_value("BDUSS")
        if not bduss:
            raise BaiduPanError(-1, "Cookie 中缺少 BDUSS，无法下载文件")
        self.download_session.headers.clear()
        self.download_session.headers.update({
            "User-Agent": "netdisk",
            "Cookie": f"BDUSS={bduss}",
        })

    def get_share_download_url(self, fs_id: int) -> str:
        """为分享文件获取临时下载地址。"""
        if not self._download_share_id or not self._download_share_uk:
            raise BaiduPanError(-1, "尚未准备分享下载会话")
        config = self._get(
            f"{BASE_URL}/share/tplconfig",
            {
                "surl": self._download_surl,
                "fields": "sign,timestamp",
                "channel": "chunlei",
                "web": "1",
                "app_id": "250528",
                "clienttype": "0",
            },
        )
        if config.get("errno") != 0:
            raise BaiduPanError(
                config.get("errno", -1),
                "获取分享下载签名失败",
            )
        sign_data = config.get("data", {})
        sign = sign_data.get("sign", "")
        timestamp = sign_data.get("timestamp", "")
        if not sign or not timestamp:
            raise BaiduPanError(-1, "分享下载签名响应不完整")
        params = {
            "sign": sign,
            "timestamp": str(timestamp),
            "bdstoken": self.get_bdstoken(),
            "channel": "chunlei",
            "web": "1",
            "app_id": "250528",
            "clienttype": "0",
        }
        payload: dict[str, str] = {
            "encrypt": "0",
            "product": "share",
            "uk": str(self._download_share_uk),
            "primaryid": str(self._download_share_id),
            "shareid": str(self._download_share_id),
            "fid_list": json.dumps([fs_id]),
            "type": "dlink",
        }
        if self._download_sekey:
            payload["extra"] = json.dumps({"sekey": self._download_sekey})
        data = self._post(f"{BASE_URL}/api/sharedownload", params, payload)
        errno = data.get("errno", -1)
        if errno != 0:
            raise BaiduPanError(errno, f"获取文件下载地址失败 (errno={errno})")
        items = data.get("list", [])
        dlink = items[0].get("dlink", "") if items else data.get("dlink", "")
        if not dlink:
            raise BaiduPanError(-1, "百度未返回文件下载地址")
        return dlink

    def download_share_file(self, fs_id: int, destination: str) -> int:
        """流式下载分享文件到 destination，返回写入字节数。"""
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                # 临时链接可能在重试期间失效，每次都重新获取。
                dlink = self.get_share_download_url(fs_id)
                written = 0
                with self.download_session.get(
                    dlink,
                    stream=True,
                    allow_redirects=True,
                    timeout=(self.timeout, 120),
                    verify=False,
                ) as response:
                    response.raise_for_status()
                    with open(destination, "wb") as output:
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                output.write(chunk)
                                written += len(chunk)
                return written
            except requests.exceptions.RequestException as exc:
                last_error = exc
                if attempt < 3:
                    delay = 2 ** attempt
                    logger.warning(
                        "下载 fs_id=%s 失败，第 %d/4 次，%d 秒后重试: %s",
                        fs_id, attempt + 1, delay, exc,
                    )
                    time.sleep(delay)
        raise BaiduPanError(-1, f"文件下载失败，已重试 4 次: {last_error}")

    def _get_cookie_value(self, name: str) -> str:
        for part in self.cookie.split(";"):
            part = part.strip()
            if part.startswith(name + "="):
                return part.split("=", 1)[1]
        return ""

    @staticmethod
    def _normalize_share_paths(
        files: list[ShareFile],
        root_files: list[ShareFile],
    ) -> None:
        """移除百度返回的分享源目录前缀，路径从分享根开始。"""
        parents = {
            posixpath.dirname(item.path.rstrip("/")) or "/"
            for item in root_files
        }
        if len(parents) != 1:
            return
        base = parents.pop()
        if base == "/":
            return
        prefix = base.rstrip("/") + "/"
        for item in files:
            if item.path.startswith(prefix):
                item.path = item.path[len(base):]

    def _recursive_list(
        self,
        share_id: int,
        share_uk: int,
        dir_path: str,
        depth: int,
    ) -> list[ShareFile]:
        """递归获取子目录文件列表。"""
        if depth <= 0:
            return []

        items = self.get_share_file_list(
            share_id, share_uk, dir_path=dir_path, root=False
        )
        result: list[ShareFile] = []
        for item in items:
            sf = self._api_item_to_share_file(item, parent_path=dir_path)
            result.append(sf)
            if sf.is_dir and depth > 1:
                result.extend(
                    self._recursive_list(share_id, share_uk, sf.path, depth - 1)
                )
        return result

    @staticmethod
    def _api_item_to_share_file(
        item: dict[str, Any],
        parent_path: str = "",
    ) -> ShareFile:
        """将 share/list API 返回的条目转为 ShareFile。"""
        name = item.get("server_filename", item.get("name", ""))
        # 百度接口可能返回整数 1，也可能返回字符串 "1"。
        is_dir = str(item.get("isdir", 0)) == "1"
        path = item.get("path", f"{parent_path}/{name}")
        if not path.startswith("/"):
            path = "/" + path
        return ShareFile(
            fs_id=int(item.get("fs_id", 0)),
            name=name,
            is_dir=is_dir,
            size=int(item.get("size", 0)),
            md5=item.get("md5", item.get("dlink_md5", "")),
            path=path,
            created_time=int(item.get("local_ctime", 0)),
            modified_time=int(item.get("local_mtime", 0)),
        )

    def close(self) -> None:
        """关闭会话。"""
        self.session.close()
        self.download_session.close()
