import json
import time
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from bdpan.client import BaiduPanError
from bdpan.cookies import validate_cookie_value, redact_sensitive
from bdpan.database import Database
from bdpan.models import ShareLink, FileEntry
from bdpan.providers import is_share_invalid_error, normalize_share
from bdpan.quark import QuarkPanClient
from bdpan.web import create_app


def test_link_detection_and_password():
    assert normalize_share('链接：https://pan.quark.cn/s/abc?pwd=1234#/list')[0:2] == ('quark', 'https://pan.quark.cn/s/abc')
    assert normalize_share('https://pan.quark.cn/s/abc 提取码：abcd')[2] == 'abcd'
    assert normalize_share('https://pan.baidu.com/s/1abc?pwd=1234')[0] == 'baidu'
    for url in ['https://pan.quark.cn.evil/s/abc', 'https://pan.quark.cn/other', 'https://example.org/s/a']:
        with pytest.raises(ValueError):
            normalize_share(url)


def test_share_invalid_error_classification():
    assert is_share_invalid_error(BaiduPanError(-8, share_invalid=True))
    assert is_share_invalid_error(BaiduPanError(-7, share_invalid=True))
    assert is_share_invalid_error(BaiduPanError(-1, "已失效", share_invalid=True))
    quark_gone = BaiduPanError(-1, "分享地址已过期")
    quark_gone.quark_code = 41019
    assert is_share_invalid_error(quark_gone)
    quark_blocked = BaiduPanError(-1, "分享受限")
    quark_blocked.quark_code = 41031
    assert is_share_invalid_error(quark_blocked)

    assert not is_share_invalid_error(BaiduPanError(-65))
    missing_password = BaiduPanError(-9)
    assert not is_share_invalid_error(missing_password)
    quark_needs_password = BaiduPanError(-1, "需要提取码")
    quark_needs_password.quark_code = 41008
    assert not is_share_invalid_error(quark_needs_password)
    assert not is_share_invalid_error(RuntimeError("network error"))


def test_cookie_validation_and_redaction():
    assert validate_cookie_value('__puus=secret', 'quark') == '__puus=secret'
    with pytest.raises(ValueError):
        validate_cookie_value('BDUSS=secret', 'quark')
    assert 'secret' not in redact_sensitive('__puus=secret; __pus=secret')


def test_provider_persistence_and_file_id_precision(tmp_path):
    db = Database(str(tmp_path / 'db.sqlite3'))
    cookie = db.add_cookie('夸克', 'hidden', 'quark')
    link = ShareLink(None, 'https://pan.quark.cn/s/a', 'a', '', 'test', 0, 0, 'active', time.time(), 0, '', cookie, 'quark')
    link.id = db.add_share_link(link)
    db.update_share_link(link)
    assert db.get_share_link(link.id).provider == 'quark'
    assert db.get_cookie(cookie).provider == 'quark'
    fid = 'quark_12345678901234567890123456789012'
    db.add_file_entries([FileEntry(None, link.id, fid, 'a', '/a', False, 1, '', 0, None)])
    assert db.get_file_entries(link.id)[0].fs_id == fid
    db.close()
    db = Database(str(tmp_path / 'db.sqlite3'))
    assert db.get_share_link(link.id).provider == 'quark'
    db.close()


def test_quark_share_endpoints_use_share_host_and_login_flag(tmp_path):
    client = QuarkPanClient('__puus=secret')
    client._request = Mock(side_effect=[
        {'stoken': 's'},
        {'list': [], 'metadata': {'_total': 0}, 'share': {'title': 'title'}},
    ])
    info = client.parse_share('https://pan.quark.cn/s/test?pwd=abcd')
    assert info.title == 'title'
    token_call = client._request.call_args_list[0]
    assert token_call.kwargs['base'] == 'https://drive-h.quark.cn/1/clouddrive'
    assert token_call.kwargs['json']['support_visit_limit_private_share'] is True
    detail_call = client._request.call_args_list[1]
    assert detail_call.kwargs['base'] == 'https://drive-h.quark.cn/1/clouddrive'
    assert detail_call.kwargs['params']['ver'] == 2
    client.close()


def test_quark_unescapes_html_entities_in_names_and_title(tmp_path):
    client = QuarkPanClient('__puus=secret')
    client._request = Mock(side_effect=[
        {'stoken': 's'},
        {
            'list': [{
                'fid': '1', 'file_name': 'A&#39;s.pdf', 'dir': False,
                'share_fid_token': 't', 'size': 1, 'updated_at': 0,
            }],
            'metadata': {'_total': 1},
            'share': {'title': 'T&amp;T'},
        },
    ])
    info = client.parse_share('https://pan.quark.cn/s/test?pwd=abcd')
    assert info.title == 'T&T'
    assert info.files[0].name == "A's.pdf"
    assert info.files[0].path == "/A's.pdf"
    client.close()


def test_quark_request_error_includes_code_and_message():
    client = QuarkPanClient('__puus=secret')
    response = Mock()
    response.raise_for_status = Mock()
    response.json = Mock(return_value={
        'status': 200,
        'code': 41030,
        'message': '当前用户封禁转存功能受限',
        'metadata': {'share_control': {'forbidden_to': 123}},
    })
    client.session.request = Mock(return_value=response)

    with pytest.raises(BaiduPanError) as ctx:
        client._request('POST', '/share/sharepage/save')

    assert '41030' in str(ctx.value)
    assert '封禁' in str(ctx.value)
    assert ctx.value.quark_code == 41030
    client.close()


@patch('bdpan.quark.time.sleep')
def test_quark_save_ban_waits_and_retries(_sleep, tmp_path):
    client = QuarkPanClient('__puus=secret')
    client.tokens['fid'] = ('token', 'parent')
    ban = BaiduPanError(-1, 'ban')
    ban.quark_code = 41030
    ban.quark_metadata = {
        'share_control': {'forbidden_to': int((time.time() + 30) * 1000)}
    }
    client._request = Mock(side_effect=[
        ban,
        {'task_id': 'task'},
        {'save_as': {'save_as_top_fids': ['saved']}},
        [{'download_url': 'https://download.example/a'}],
    ])
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.iter_content.return_value = [b'hello']
    client.session.get = Mock(return_value=response)

    assert client.download_share_file('quark_fid', str(tmp_path / 'out')) == 5
    assert (tmp_path / 'out').read_bytes() == b'hello'
    _sleep.assert_called()
    client.close()


def test_quark_recursive_pagination_and_download(tmp_path):
    client = QuarkPanClient('__puus=secret')
    root = [{'fid': str(i), 'file_name': f'{i}.txt', 'dir': False, 'share_fid_token': f't{i}'} for i in range(100)]
    client._request = Mock(side_effect=[{'stoken': 's'}, {'list': root}, {'list': [{'fid': 'folder', 'file_name': 'folder', 'dir': True}]}, {'list': [{'fid': 'child', 'file_name': 'child.txt', 'dir': False, 'share_fid_token': 'ct'}]}])
    info = client.parse_share('https://pan.quark.cn/s/test?pwd=abcd')
    assert len(info.files) == 102
    assert info.files[-1].path == '/folder/child.txt'
    client._request = Mock(side_effect=[{'task_id': 'task'}, {'save_as': {'save_as_top_fids': ['saved']}}, [{'download_url': 'https://download.example/a'}]])
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.iter_content.return_value = [b'hello']
    client.session.get = Mock(return_value=response)
    assert client.download_share_file('quark_child', str(tmp_path / 'out')) == 5
    assert (tmp_path / 'out').read_bytes() == b'hello'
    payload = client._request.call_args_list[0].kwargs['json']
    assert payload['pdir_fid'] == 'folder'
    assert payload['fid_token_list'] == ['ct']
    client.close()


def test_incomplete_index_fails():
    client = QuarkPanClient('__puus=secret')
    client._request = Mock(side_effect=[{'stoken': 's'}, {}])
    with pytest.raises(Exception, match='不完整'):
        client.parse_share('https://pan.quark.cn/s/test')
    client.close()


def test_quark_api_cookie_isolation(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        client.post('/api/login', json={'username': 'admin', 'password': 'admin123'})
        result = client.post('/api/cookies', json={'name': '夸克', 'provider': 'quark', 'cookie': '__puus=secret'})
        assert result.status_code == 200
        cookie = result.json()['id']
        settings = client.get('/api/settings')
        assert 'secret' not in settings.text
        assert next(item for item in settings.json()['cookies'] if item['id'] == cookie)['provider'] == 'quark'
        assert client.post('/api/shares', json={'url': 'https://pan.baidu.com/s/1test', 'cookie_id': cookie}).status_code == 400
        assert client.put(f'/api/cookies/{cookie}', json={'name': '夸克新', 'cookie': '__puus=new'}).status_code == 200
        assert client.post('/api/shares', json={'url': 'https://unsupported.example/s/abc', 'cookie_id': cookie}).status_code == 400

def test_quark_add_refresh_and_browse(tmp_path, monkeypatch):
    from bdpan.client import ShareInfo, ShareFile
    fake = Mock()
    fake.cookie = '__puus=test'
    fake.parse_share.return_value = ShareInfo(0, 0, 'abc', 'Quark files', [ShareFile('quark_fid', 'test.txt', False, 5, '', '/test.txt', 0, 0)], fake.cookie)
    monkeypatch.setattr('bdpan.web.create_client', lambda provider, cookie: fake)
    app = create_app(tmp_path)
    with TestClient(app) as client:
        client.post('/api/login', json={'username': 'admin', 'password': 'admin123'})
        cookie = client.post('/api/cookies', json={'name': 'Quark smoke', 'provider': 'quark', 'cookie': fake.cookie}).json()['id']
        response = client.post('/api/shares', json={'url': 'https://pan.quark.cn/s/abc?pwd=abcd', 'cookie_id': cookie})
        assert response.status_code == 200
        def wait(task_id):
            for _ in range(100):
                state = vars(app.state.tasks.tasks[task_id])
                if state['status'] in ('success', 'failed'):
                    assert state['status'] == 'success', state
                    return
                time.sleep(.01)
            pytest.fail('Task timed out')
        wait(response.json()['task_id'])
        state = client.get('/api/state').json()
        share = next(link for link in state['links'] if link['provider'] == 'quark')
        fake.parse_share.assert_called_with('https://pan.quark.cn/s/abc', 'abcd')
        assert client.get(f"/api/shares/{share['id']}/entries").json()[0]['fs_id'] == 'quark_fid'
        wait(client.post(f"/api/shares/{share['id']}/refresh").json()['task_id'])
        assert client.put(f"/api/shares/{share['id']}", json={'url': 'https://pan.baidu.com/s/1test'}).status_code == 400
        assert client.get('/').status_code == 200


def test_refresh_gone_share_marks_link_invalid(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        client.post('/api/login', json={'username': 'admin', 'password': 'admin123'})
        cookie = client.post('/api/cookies', json={
            'name': 'Baidu', 'provider': 'baidu', 'cookie': 'BDUSS=test',
        }).json()['id']
        store = Database(app.state.db_path)
        link_id = store.add_share_link(ShareLink(
            None, 'https://pan.baidu.com/s/1test', 'test', '', '已失效分享',
            1, 2, 'active', time.time(), time.time(), '', cookie, 'baidu',
        ))
        store.close()

        fake = Mock()
        fake.cookie = 'BDUSS=test'
        fake.parse_share.side_effect = BaiduPanError(
            -8, "该分享已经过期", share_invalid=True,
        )
        monkeypatch.setattr('bdpan.web.BaiduPanClient', lambda cookie: fake)

        task_id = client.post(f'/api/shares/{link_id}/refresh').json()['task_id']
        for _ in range(100):
            state = vars(app.state.tasks.tasks[task_id])
            if state['status'] in ('success', 'failed'):
                assert state['status'] == 'failed'
                break
            time.sleep(.01)
        else:
            pytest.fail('Task timed out')

        store = Database(app.state.db_path)
        try:
            link = store.get_share_link(link_id)
            assert link.status == 'invalid'
        finally:
            store.close()


def test_refresh_transient_error_keeps_active_status(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        client.post('/api/login', json={'username': 'admin', 'password': 'admin123'})
        cookie = client.post('/api/cookies', json={
            'name': 'Baidu', 'provider': 'baidu', 'cookie': 'BDUSS=test',
        }).json()['id']
        store = Database(app.state.db_path)
        link_id = store.add_share_link(ShareLink(
            None, 'https://pan.baidu.com/s/1test', 'test', '', '风控分享',
            1, 2, 'active', time.time(), time.time(), '', cookie, 'baidu',
        ))
        store.close()

        fake = Mock()
        fake.cookie = 'BDUSS=test'
        fake.parse_share.side_effect = BaiduPanError(-65)
        monkeypatch.setattr('bdpan.web.BaiduPanClient', lambda cookie: fake)

        task_id = client.post(f'/api/shares/{link_id}/refresh').json()['task_id']
        for _ in range(100):
            state = vars(app.state.tasks.tasks[task_id])
            if state['status'] in ('success', 'failed'):
                break
            time.sleep(.01)

        store = Database(app.state.db_path)
        try:
            assert store.get_share_link(link_id).status == 'active'
        finally:
            store.close()
