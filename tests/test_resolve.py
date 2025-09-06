import pytest
from ota_client import OtaClient, OTAError


class Resp:
    def __init__(self, data=None, status_code=200, body=""):
        self._data = data
        self.status_code = status_code
        self.text = body

    def json(self):
        return self._data

    def close(self):
        pass

def test_resolve_stable():
    cfg = {"owner": "o", "repo": "r", "channel": "stable"}
    client = OtaClient(cfg)
    mapping = {
        "https://api.github.com/repos/o/r/releases/latest": Resp({"tag_name": "v1"}),
        "https://api.github.com/repos/o/r/git/ref/tags/v1": Resp({"object": {"type": "commit", "sha": "abc"}}),
    }
    client._get = lambda url, raw=False: mapping[url]
    ref, commit, mode = client.resolve_stable()
    assert ref == "v1"
    assert commit == "abc"
    assert mode == "tag"

def test_resolve_developer():
    cfg = {"owner": "o", "repo": "r", "channel": "developer", "branch": "main"}
    client = OtaClient(cfg)
    mapping = {
        "https://api.github.com/repos/o/r/git/ref/heads/main": Resp({"object": {"type": "commit", "sha": "def"}}),
    }
    client._get = lambda url, raw=False: mapping[url]
    branch, commit = client.resolve_developer()
    assert branch == "main"
    assert commit == "def"


def test_resolve_stable_no_release():
    cfg = {"owner": "o", "repo": "r", "channel": "stable"}
    client = OtaClient(cfg)
    client._get = lambda url, raw=False: Resp(status_code=404, body="Not Found")
    with pytest.raises(OTAError) as excinfo:
        client.resolve_stable()
    assert "No release found for o/r" in str(excinfo.value)


def test_resolve_stable_fallback():
    cfg = {"owner": "o", "repo": "r", "channel": "stable", "fallback_channel": "developer", "branch": "main"}
    client = OtaClient(cfg)
    mapping = {
        "https://api.github.com/repos/o/r/releases/latest": Resp(status_code=404, body="Not Found"),
        "https://api.github.com/repos/o/r/git/ref/heads/main": Resp({"object": {"type": "commit", "sha": "def"}}),
    }
    client._get = lambda url, raw=False: mapping[url]
    ref, commit, mode = client.resolve_stable()
    assert ref == "main"
    assert commit == "def"
    assert mode == "branch"
