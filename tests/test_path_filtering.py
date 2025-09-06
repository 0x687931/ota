import ota
from ota import OTA


class Resp:
    def __init__(self, data):
        self._data = data
    def json(self):
        return self._data
    def close(self):
        pass


def test_is_permitted_filters(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cfg = {
        'owner': 'o',
        'repo': 'r',
        'allow': ['/allowed/', '/exact.txt'],
        'ignore': ['/allowed/skip/'],
    }
    client = OTA(cfg)
    assert client._allow == ('allowed', 'exact.txt')
    assert client._ignore == ('allowed/skip',)
    assert client._is_permitted('exact.txt')
    assert client._is_permitted('allowed/file.txt')
    assert not client._is_permitted('other/file.txt')
    assert not client._is_permitted('allowed/skip/file.txt')


def test_stable_with_manifest_skips_disallowed(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cfg = {'owner': 'o', 'repo': 'r', 'allow': ['keep']}
    client = OTA(cfg)
    manifest = {
        'version': 'v1',
        'files': [
            {'path': 'keep/file.txt'},
            {'path': 'skip/file.txt'},
        ],
        'deletes': ['keep/remove_me', 'skip/delete_me'],
    }
    rel_json = {'assets': [{'name': 'manifest.json', 'browser_download_url': 'http://example/manifest.json'}]}
    monkeypatch.setattr(client, '_get', lambda url, raw=True: Resp(manifest))
    monkeypatch.setattr(client, '_verify_manifest_signature', lambda m: None)
    monkeypatch.setattr(client, '_read_state', lambda: None)
    monkeypatch.setattr(ota, 'ensure_dirs', lambda path: None)
    downloads = []
    monkeypatch.setattr(client, '_download_asset', lambda url, dest, **kw: downloads.append(dest))
    called = {}
    def fake_stage_and_swap(ref, commit, deletes=None):
        called['deletes'] = deletes
    monkeypatch.setattr(client, 'stage_and_swap', fake_stage_and_swap)
    res = client._stable_with_manifest(rel_json, 'v1', 'abc')
    assert downloads == [client._stage_path('keep/file.txt')]
    assert called['deletes'] == ['keep/remove_me']
    assert res == {'updated': True}


def test_iter_candidates_filters(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cfg = {'owner': 'o', 'repo': 'r', 'allow': ['keep'], 'ignore': ['keep/ignore']}
    client = OTA(cfg)
    tree = [
        {'path': 'keep/file.txt', 'type': 'blob', 'size': '10'},
        {'path': 'keep/ignore/file.txt', 'type': 'blob', 'size': '10'},
        {'path': 'skip/file.txt', 'type': 'blob', 'size': '10'},
        {'path': 'keep/empty.txt', 'type': 'blob', 'size': '0'},
        {'path': 'keep/dir', 'type': 'tree', 'size': '0'},
    ]
    paths = [e['path'] for e in client.iter_candidates(tree)]
    assert paths == ['keep/file.txt']
