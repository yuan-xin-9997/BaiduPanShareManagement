import json
import time
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from bdpan.cookies import validate_cookie_value, redact_sensitive
from bdpan.database import Database
from bdpan.models import ShareLink, FileEntry
from bdpan.providers import normalize_share
from bdpan.quark import QuarkPanClient
from bdpan.web import create_app


def test_link_detection_and_password():
    assert normalize_share('链接：https://pan.quark.cn/s/abc?pwd=1234#/list')[0:2] == ('quark', 'https://pan.quark.cn/s/abc')
    assert normalize_share('https://pan.quark.cn/s/abc 提取码：abcd')[2] == 'abcd'
    assert normalize_share('https://pan.baidu.com/s/1abc?pwd=1234')[0] == 'baidu'
    for url in ['https://pan.quark.cn.evil/s/abc', 'https://pan.quark.cn/other', 'https://example.org/s/a']:
        with pytest.raises(ValueError):
            normalize_share(url)


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
