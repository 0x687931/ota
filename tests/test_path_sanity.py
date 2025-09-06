import pytest
from ota import OTA, OTAError


class Resp:
    def __init__(self, data):
        self._data = data
    def json(self):
        return self._data
    def close(self):
        pass


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return OTA({'owner': 'o', 'repo': 'r'})


@pytest.mark.parametrize('path', [
    '../bad',
    '/bad',
    'dir/../bad',
    'dir/./bad',
    'dir//bad',
])
def test_stage_path_rejects(client, path):
    with pytest.raises(OTAError):
        client._stage_path(path)


@pytest.mark.parametrize('path', [
    '../bad',
    '/bad',
    'dir/../bad',
    'dir/./bad',
    'dir//bad',
])
def test_backup_path_rejects(client, path):
    with pytest.raises(OTAError):
        client._backup_path(path)


@pytest.mark.parametrize('path', [
    '../bad',
    '/bad',
    'dir/../bad',
    'dir/./bad',
    'dir//bad',
])
def test_manifest_file_rejects(client, path, monkeypatch):
    manifest = {
        'version': 'v1',
        'files': [{'path': path}],
    }
    rel_json = {
        'assets': [{'name': 'manifest.json', 'browser_download_url': 'http://example/manifest.json'}]
    }
    monkeypatch.setattr(client, '_get', lambda url, raw=True: Resp(manifest))
    monkeypatch.setattr(client, '_verify_manifest_signature', lambda m: None)
    monkeypatch.setattr(client, '_read_state', lambda: None)
    with pytest.raises(OTAError):
        client._stable_with_manifest(rel_json, 'v1', 'abc')
