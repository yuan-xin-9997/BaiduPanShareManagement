"""夸克 Web 接口适配：分享索引、转存后下载。"""
import os
import posixpath
import re
import time

import requests

from .client import BaiduPanError, ShareFile, ShareInfo
from .providers import normalize_share


class QuarkPanClient:
    def __init__(self, cookie: str, timeout: int = 30):
        self.cookie, self.timeout = cookie, timeout
        self.base_url = os.environ.get("QUARK_API_URL", "https://drive-pc.quark.cn/1/clouddrive").rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Cookie": cookie, "Referer": "https://pan.quark.cn/", "User-Agent": "Mozilla/5.0"})
        self.pwd_id = self.stoken = self.password = ""
        self.tokens = {}
        self.saved_fids = {}

    def _request(self, method, path, **kwargs):
        try:
            response = self.session.request(method, self.base_url + path, params={"pr": "ucpro", "fr": "pc", **kwargs.pop("params", {})}, timeout=self.timeout, **kwargs)
            response.raise_for_status()
            result = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise BaiduPanError(-1, "夸克网盘请求失败，请检查网络或重新登录") from exc
        if not isinstance(result, dict) or result.get("code", 0) != 0 or result.get("status", 200) != 200:
            raise BaiduPanError(-1, "夸克网盘拒绝请求，请检查提取码、分享状态或 Cookie")
        data = result.get("data")
        if not isinstance(data, (dict, list)):
            raise BaiduPanError(-1, "夸克网盘响应格式异常")
        return data

    def get_bdstoken(self):
        self._request("GET", "/file/sort", params={"pdir_fid": "0", "_page": 1, "_size": 1})
        return "valid"

    def prepare_share_download(self, raw_url, password=""):
        provider, url, embedded = normalize_share(raw_url)
        if provider != "quark":
            raise ValueError("分享链接与网盘类型不匹配")
        self.password = password or embedded
        self.pwd_id = url.rsplit("/", 1)[-1]
        data = self._request("POST", "/share/sharepage/token", json={"pwd_id": self.pwd_id, "passcode": password or embedded})
        self.stoken = data.get("stoken", "")
        if not self.stoken:
            raise BaiduPanError(-1, "夸克分享令牌缺失")

    def parse_share(self, raw_url, password="", recursive=True, max_depth=20):
        self.prepare_share_download(raw_url, password)
        files = []
        title = ""

        def visit(parent, path, depth):
            nonlocal title
            page = 1
            while True:
                data = self._request("GET", "/share/sharepage/detail", params={"pwd_id": self.pwd_id, "stoken": self.stoken, "pdir_fid": parent, "_page": page, "_size": 100, "_sort": "file_type:asc,file_name:asc", "_fetch_total": 1})
                if not isinstance(data, dict) or not isinstance(data.get("list"), list):
                    raise BaiduPanError(-1, "夸克目录响应不完整，已停止索引")
                title = title or data.get("share", {}).get("title", "")
                items = data["list"]
                for item in items:
                    fid, name = str(item["fid"]), str(item["file_name"])
                    if not re.fullmatch(r"[A-Za-z0-9_-]+", fid):
                        raise BaiduPanError(-1, "夸克文件 ID 格式异常")
                    if not name or name in (".", "..") or "/" in name or "\\" in name:
                        raise BaiduPanError(-1, "夸克目录包含不安全的文件名")
                    remote = posixpath.join(path, name)
                    directory = item.get("dir") in (True, 1, "1")
                    self.tokens[fid] = (item.get("share_fid_token", ""), parent)
                    files.append(ShareFile("quark_" + fid, name, directory, int(item.get("size", 0)), item.get("md5", ""), remote, 0, int(item.get("updated_at", 0)) // 1000))
                    if directory and recursive:
                        if depth <= 1:
                            raise BaiduPanError(-1, "夸克目录超过最大深度，已停止索引以避免不完整同步")
                        visit(fid, remote, depth - 1)
                if not items:
                    break
                total = data.get("metadata", {}).get("_total")
                if (total is not None and page * 100 >= int(total)) or (total is None and len(items) < 100):
                    break
                page += 1
        visit("0", "/", max_depth)
        return ShareInfo(0, 0, self.pwd_id, title or (files[0].name if files else self.pwd_id), files, self.cookie)

    def download_share_file(self, fs_id, destination):
        fid = str(fs_id).removeprefix("quark_")
        if fid not in self.tokens:
            self.parse_share(f"https://pan.quark.cn/s/{self.pwd_id}", self.password, recursive=True)
        if fid not in self.tokens:
            raise BaiduPanError(-1, "夸克分享中的文件已不存在，请刷新索引")
        token, parent = self.tokens[fid]
        if not token:
            raise BaiduPanError(-1, "夸克文件转存令牌缺失")
        # 夸克只允许下载自己网盘的文件；转存保留在账号中，便于重试和人工核对。
        target = self.saved_fids.get(fid)
        if not target:
            saved = self._request("POST", "/share/sharepage/save", json={"fid_list": [fid], "fid_token_list": [token], "pwd_id": self.pwd_id, "stoken": self.stoken, "pdir_fid": parent, "to_pdir_fid": "0", "scene": "link"})
            task_id = saved.get("task_id")
            if not task_id:
                raise BaiduPanError(-1, "夸克未返回转存任务 ID")
            target = None
            for retry in range(30):
                task = self._request("GET", "/task", params={"task_id": task_id, "retry_index": retry})
                targets = task.get("save_as", {}).get("save_as_top_fids", [])
                if targets:
                    target = targets[0]
                    break
                if task.get("status") == 2:
                    break
                time.sleep(1)
            if not target:
                raise BaiduPanError(-1, "夸克转存未完成，请检查网盘容量或稍后重试")
            self.saved_fids[fid] = target
        data = self._request("POST", "/file/download", json={"fids": [target]})
        if not isinstance(data, list) or not data or not data[0].get("download_url"):
            raise BaiduPanError(-1, "夸克未返回下载地址")
        written = 0
        with self.session.get(data[0]["download_url"], stream=True, timeout=(self.timeout, 120)) as response:
            response.raise_for_status()
            with open(destination, "wb") as output:
                for chunk in response.iter_content(1024 * 1024):
                    output.write(chunk)
                    written += len(chunk)
        return written

    def close(self):
        self.session.close()
