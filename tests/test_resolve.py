import pytest
from ota_client import OtaClient


class Resp:
    def __init__(self, data):
        self._data = data
    def json(self):
        return self._data
    def close(self):
        pass

def test_resolve_stable():
    cfg = {"owner": "o", "repo": "r", "channel": "stable"}
    client = OtaClient(cfg)
    mapping = {
        "https://api.github.com/repos/o/r/releases/latest": {"tag_name": "v1"},
        "https://api.github.com/repos/o/r/git/ref/tags/v1": {"object": {"type": "commit", "sha": "abc"}},
    }
    client._get = lambda url, raw=False: Resp(mapping[url])
    tag, commit = client.resolve_stable()
    assert tag == "v1"
    assert commit == "abc"

def test_resolve_developer():
    cfg = {"owner": "o", "repo": "r", "channel": "developer", "branch": "main"}
    client = OtaClient(cfg)
    mapping = {
        "https://api.github.com/repos/o/r/git/ref/heads/main": {"object": {"type": "commit", "sha": "def"}},
    }
    client._get = lambda url, raw=False: Resp(mapping[url])
    branch, commit = client.resolve_developer()
    assert branch == "main"
    assert commit == "def"
