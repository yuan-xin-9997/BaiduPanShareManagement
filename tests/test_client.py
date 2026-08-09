import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import Mock, patch

from bdpan.client import BaiduPanClient, BaiduPanError, ShareFile


class BdstokenTests(unittest.TestCase):
    def test_disables_response_compression_for_baidu_web_requests(self) -> None:
        client = BaiduPanClient(cookie="BDUSS=test")

        self.assertEqual(client.session.headers["Accept-Encoding"], "identity")

    def test_uses_current_web_app_id(self) -> None:
        client = BaiduPanClient(cookie="BDUSS=test")
        client._get = Mock(return_value={
            "errno": 0,
            "result": {"bdstoken": "test-token"},
        })

        self.assertEqual(client.get_bdstoken(), "test-token")
        self.assertEqual(client._get.call_args.args[1]["app_id"], "250528")

    def test_reports_baidu_errno_and_cookie_guidance(self) -> None:
        client = BaiduPanClient(cookie="BDUSS=expired")
        client._get = Mock(return_value={"errno": -6})

        with self.assertRaisesRegex(BaiduPanError, r"errno=-6.*Cookie"):
            client.get_bdstoken()

    def test_reports_missing_token_without_key_error(self) -> None:
        client = BaiduPanClient(cookie="BDUSS=test")
        client._get = Mock(return_value={"errno": 0, "result": {}})

        with self.assertRaisesRegex(BaiduPanError, "没有 bdstoken"):
            client.get_bdstoken()

    def test_rejects_non_object_json_response_with_readable_error(self) -> None:
        class Response:
            def json(self):
                return "waf page"

        client = BaiduPanClient(cookie="BDUSS=test")
        client.session.get = Mock(return_value=Response())

        with self.assertRaisesRegex(BaiduPanError, "响应格式异常.*str"):
            client._get("https://pan.baidu.com/api/test")

    def test_share_token_falls_back_to_empty_on_login_risk_control(self) -> None:
        client = BaiduPanClient(cookie="BDUSS=test")
        client.get_bdstoken = Mock(side_effect=BaiduPanError(-6, "risk control"))

        self.assertEqual(client._get_share_bdstoken(), "")

    def test_share_token_keeps_unrelated_errors(self) -> None:
        client = BaiduPanClient(cookie="BDUSS=test")
        client.get_bdstoken = Mock(side_effect=BaiduPanError(105, "missing"))

        with self.assertRaises(BaiduPanError):
            client._get_share_bdstoken()


class VerifyPasswordTests(unittest.TestCase):
    def _client(self) -> BaiduPanClient:
        client = BaiduPanClient(cookie="test-cookie")
        client.get_bdstoken = Mock(return_value="test-token")
        client._post = Mock(return_value={"errno": 0, "randsk": "test-randsk"})
        return client

    def test_strips_share_path_prefix(self) -> None:
        client = self._client()

        self.assertEqual(
            client.verify_password("1pcPgQlsDftMBE6CZ5ZIqsQ", "6666"),
            "test-randsk",
        )

        _, kwargs = client._post.call_args
        self.assertEqual(kwargs["params"]["surl"], "pcPgQlsDftMBE6CZ5ZIqsQ")
        self.assertEqual(kwargs["data"]["pwd"], "6666")

    def test_keeps_api_surl_without_prefix(self) -> None:
        client = self._client()

        client.verify_password("pcPgQlsDftMBE6CZ5ZIqsQ", "6666")

        _, kwargs = client._post.call_args
        self.assertEqual(kwargs["params"]["surl"], "pcPgQlsDftMBE6CZ5ZIqsQ")

    def test_uses_empty_token_when_account_cookie_is_risk_controlled(self) -> None:
        client = BaiduPanClient(cookie="BDUSS=test")
        client.get_bdstoken = Mock(side_effect=BaiduPanError(-6, "risk control"))
        client._post = Mock(return_value={"errno": 0, "randsk": "test-randsk"})

        client.verify_password("1example", "6666")

        self.assertEqual(client._post.call_args.kwargs["params"]["bdstoken"], "")

    def test_errno_minus_65_explains_security_or_rate_limit(self) -> None:
        client = self._client()
        client._post = Mock(return_value={"errno": -65})

        with self.assertRaisesRegex(
            BaiduPanError, r"安全验证.*频率限制"
        ):
            client.verify_password("1example", "6666")


class SharePageParsingTests(unittest.TestCase):
    def test_parses_current_yun_data_format(self) -> None:
        client = BaiduPanClient(cookie="test-cookie")
        client._get_html = Mock(return_value=(
            'window.yunData={share_uk:"1732809698", shareid:"11564703787"};'
            '{"file_list":[{"fs_id":69097898701227,'
            '"server_filename":"7大投行","isdir":1,"size":0}]}'
        ))

        result = client._parse_share_page("https://pan.baidu.com/s/1example")

        self.assertEqual(result["shareid"], "11564703787")
        self.assertEqual(result["share_uk"], "1732809698")
        self.assertEqual(result["filenames"], ["7大投行"])


class ApiItemParsingTests(unittest.TestCase):
    def test_string_isdir_is_parsed_as_directory(self) -> None:
        result = BaiduPanClient._api_item_to_share_file({
            "fs_id": "69097898701227",
            "server_filename": "7大投行",
            "isdir": "1",
            "path": "/我的虚拟产品/7大投行",
            "size": "0",
        })

        self.assertTrue(result.is_dir)
        self.assertEqual(result.path, "/我的虚拟产品/7大投行")

    def test_share_paths_are_relative_to_share_root(self) -> None:
        root = ShareFile(1, "7大投行", True, 0, "", "/我的虚拟产品/7大投行", 0, 0)
        child = ShareFile(2, "高盛", True, 0, "", "/我的虚拟产品/7大投行/高盛", 0, 0)

        BaiduPanClient._normalize_share_paths([root, child], [root])

        self.assertEqual(root.path, "/7大投行")
        self.assertEqual(child.path, "/7大投行/高盛")

    def test_share_list_reads_all_pages(self) -> None:
        client = BaiduPanClient(cookie="test-cookie")
        client.get_bdstoken = Mock(return_value="test-token")
        first_page = [{"fs_id": i} for i in range(100)]
        second_page = [{"fs_id": 100}]
        client._get = Mock(side_effect=[
            {"errno": 0, "list": first_page},
            {"errno": 0, "list": second_page},
        ])

        result = client.get_share_file_list(1, 2, dir_path="/7大投行/高盛", root=False)

        self.assertEqual(len(result), 101)
        self.assertEqual(client._get.call_args_list[0].args[1]["page"], 1)
        self.assertEqual(client._get.call_args_list[1].args[1]["page"], 2)
        self.assertEqual(client._get.call_args_list[0].args[1]["order"], "time")

    def test_failed_page_falls_back_to_single_item_requests(self) -> None:
        client = BaiduPanClient(cookie="test-cookie")
        client.get_bdstoken = Mock(return_value="test-token")

        def get_page(_url, params):
            if params["num"] == 100 and params["page"] == 1:
                return {"errno": 115}
            if params["num"] == 1:
                return {"errno": 0, "list": [{"fs_id": params["page"]}]}
            return {"errno": 0, "list": []}

        client._get = Mock(side_effect=get_page)

        result = client.get_share_file_list(1, 2, root=True)

        self.assertEqual(len(result), 100)
        self.assertEqual(result[0]["fs_id"], 1)
        self.assertEqual(result[-1]["fs_id"], 100)

    def test_rejects_non_list_share_list_payload(self) -> None:
        client = BaiduPanClient(cookie="test-cookie")
        client.get_bdstoken = Mock(return_value="test-token")
        client._get = Mock(return_value={"errno": 0, "list": "not-a-list"})

        with self.assertRaisesRegex(BaiduPanError, "share/list 响应列表格式异常"):
            client.get_share_file_list(1, 2, root=True)

    def test_rejects_non_object_share_list_item(self) -> None:
        with self.assertRaisesRegex(BaiduPanError, "分享目录条目响应格式异常"):
            BaiduPanClient._api_item_to_share_file("bad-item")


class ShareDownloadTests(unittest.TestCase):
    def test_get_share_download_url_sends_complete_share_context(self) -> None:
        client = BaiduPanClient(cookie="test-cookie")
        client._download_share_id = 11564703787
        client._download_share_uk = 1732809698
        client._download_sekey = "test-sekey"
        client._download_surl = "1example"
        client.get_bdstoken = Mock(return_value="test-token")
        client._get = Mock(return_value={
            "errno": 0,
            "data": {"sign": "test-sign", "timestamp": 1234567890},
        })
        client._post = Mock(return_value={
            "errno": 0,
            "list": [{"dlink": "https://example.test/file"}],
        })

        result = client.get_share_download_url(307288499661)

        self.assertEqual(result, "https://example.test/file")
        _, params, payload = client._post.call_args.args
        self.assertEqual(params["app_id"], "250528")
        self.assertEqual(params["sign"], "test-sign")
        self.assertEqual(params["timestamp"], "1234567890")
        self.assertEqual(params["clienttype"], "0")
        self.assertEqual(payload["type"], "dlink")
        self.assertEqual(payload["shareid"], "11564703787")
        self.assertIn("test-sekey", payload["extra"])

    def test_rejects_non_object_download_list_item(self) -> None:
        client = BaiduPanClient(cookie="test-cookie")
        client._download_share_id = 11564703787
        client._download_share_uk = 1732809698
        client._download_surl = "1example"
        client.get_bdstoken = Mock(return_value="test-token")
        client._get = Mock(return_value={
            "errno": 0,
            "data": {"sign": "test-sign", "timestamp": 1234567890},
        })
        client._post = Mock(return_value={"errno": 0, "list": ["bad-item"]})

        with self.assertRaisesRegex(BaiduPanError, "文件下载地址响应格式异常"):
            client.get_share_download_url(307288499661)

    def test_decodes_double_encoded_json_string_list(self) -> None:
        # 百度偶尔把 sharedownload 的 list 二次编码为 JSON 字符串，
        # 例如 errno=0 但 list="[{\"dlink\":\"...\"}]"。应当解码后正常取用。
        client = BaiduPanClient(cookie="test-cookie")
        client._download_share_id = 11564703787
        client._download_share_uk = 1732809698
        client._download_surl = "1example"
        client.get_bdstoken = Mock(return_value="test-token")
        client._get = Mock(return_value={
            "errno": 0,
            "data": {"sign": "test-sign", "timestamp": 1234567890},
        })
        client._post = Mock(return_value={
            "errno": 0,
            "list": json.dumps([{"dlink": "https://example.test/file"}]),
        })

        self.assertEqual(
            client.get_share_download_url(307288499661),
            "https://example.test/file",
        )

    @patch("bdpan.client.time.sleep")
    def test_retries_on_risk_control_string_then_succeeds(self, _sleep) -> None:
        # 百度风控时 sharedownload 返回 errno=0 但 list 为密文字符串（非 JSON）；
        # 重试后恢复正常数组，应成功取到 dlink。
        client = BaiduPanClient(cookie="test-cookie")
        client._download_share_id = 11564703787
        client._download_share_uk = 1732808968
        client._download_surl = "1example"
        client.get_bdstoken = Mock(return_value="test-token")
        client._get = Mock(return_value={
            "errno": 0,
            "data": {"sign": "test-sign", "timestamp": 1234567890},
        })
        client._post = Mock(side_effect=[
            {"errno": 0, "list": "lcLSYqsqWZI7非JSON密文响应"},
            {"errno": 0, "list": "lcLSYqsqWZI7非JSON密文响应"},
            {"errno": 0, "list": [{"dlink": "https://example.test/file"}]},
        ])

        self.assertEqual(
            client.get_share_download_url(307288499661),
            "https://example.test/file",
        )
        self.assertEqual(client._post.call_count, 3)

    @patch("bdpan.client.time.sleep")
    def test_raises_risk_control_after_retries_exhausted(self, _sleep) -> None:
        # 风控持续未恢复时，重试耗尽后按 -1 抛出（STANDARD 退避，上限 6h），
        # 而非 -65（RISK 退避，上限 24h）：密文为间歇性抖动，较短退避更利于恢复。
        client = BaiduPanClient(cookie="test-cookie")
        client._download_share_id = 11564703787
        client._download_share_uk = 1732808968
        client._download_surl = "1example"
        client.get_bdstoken = Mock(return_value="test-token")
        client._get = Mock(return_value={
            "errno": 0,
            "data": {"sign": "test-sign", "timestamp": 1234567890},
        })
        client._post = Mock(return_value={
            "errno": 0,
            "list": "lcLSYqsqWZI7非JSON密文响应",
        })

        with self.assertRaises(BaiduPanError) as ctx:
            client.get_share_download_url(307288499661)
        self.assertEqual(ctx.exception.code, -1)
        # risk_retries = 4
        self.assertEqual(client._post.call_count, 4)

    def test_json_list_decodes_json_string_and_rejects_garbage(self) -> None:
        self.assertEqual(
            BaiduPanClient._json_list('[{"a": 1}]', "sharedownload"),
            [{"a": 1}],
        )
        # 空 JSON 列表字符串也应还原为空列表
        self.assertEqual(BaiduPanClient._json_list("[]", "sharedownload"), [])
        # 非 JSON 字符串仍按格式异常处理
        with self.assertRaisesRegex(BaiduPanError, "sharedownload 响应列表格式异常"):
            BaiduPanClient._json_list("not-a-list", "sharedownload")
        # JSON 但非列表同样拒绝
        with self.assertRaisesRegex(BaiduPanError, "sharedownload 响应列表格式异常"):
            BaiduPanClient._json_list('"a-string"', "sharedownload")

    def test_dlink_request_uses_only_bduss_and_netdisk_user_agent(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size):
                yield b"real-pdf"

        client = BaiduPanClient(cookie="BDUSS=secret; STOKEN=should-not-leak")
        self.assertFalse(client.download_session.trust_env)
        client.get_share_download_url = Mock(return_value="https://d.pcs.baidu.com/file")
        client.download_session.get = Mock(return_value=Response())

        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp, "report.pdf")
            written = client.download_share_file(1, str(destination))

        self.assertEqual(written, 8)
        self.assertNotIn("headers", client.download_session.get.call_args.kwargs)

    def test_download_retries_proxy_failure_with_fresh_dlink(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size):
                yield b"pdf-data"

        client = BaiduPanClient(cookie="BDUSS=secret")
        client.get_share_download_url = Mock(side_effect=[
            "https://d.pcs.baidu.com/expired",
            "https://d.pcs.baidu.com/fresh",
        ])
        client.download_session.get = Mock(side_effect=[
            __import__("requests").exceptions.ProxyError("proxy disconnected"),
            Response(),
        ])

        with tempfile.TemporaryDirectory() as tmp, patch("bdpan.client.time.sleep"):
            written = client.download_share_file(1, str(Path(tmp, "report.pdf")))

        self.assertEqual(written, 8)
        self.assertEqual(client.get_share_download_url.call_count, 2)
