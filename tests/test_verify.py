import os
import hashlib
import io
from ota_client import OtaClient, git_blob_sha1_stream


class Resp:
    def __init__(self, data):
        self.data = data
        self.pos = 0
    def read(self, n):
        if self.pos >= len(self.data):
            return b""
        chunk = self.data[self.pos : self.pos + n]
        self.pos += len(chunk)
        return chunk
    def close(self):
        pass


class RawResp:
    def __init__(self, data):
        self.raw = io.BytesIO(data)

    def close(self):
        pass


def test_stream_and_verify(tmp_path):
    data = b"hello"
    size = len(data)
    sha = hashlib.sha1(b"blob " + str(size).encode() + b"\0" + data).hexdigest()
    entry = {"path": "file.txt", "size": size, "sha": sha, "type": "blob"}
    cfg = {"owner": "o", "repo": "r", "chunk": 4}
    client = OtaClient(cfg)
    client.stage_dir = str(tmp_path / ".stage")
    client.backup_dir = str(tmp_path / ".backup")
    client.ensure_dirs(client.stage_dir)
    client.ensure_dirs(client.backup_dir)
    client._get = lambda url, raw=False: Resp(data)
    assert client.stream_and_verify(entry, "ref")
    staged = tmp_path / ".stage" / "file.txt"
    assert staged.read_bytes() == data


def test_stream_and_verify_requests_raw(tmp_path):
    data = b"hello"
    size = len(data)
    sha = hashlib.sha1(b"blob " + str(size).encode() + b"\0" + data).hexdigest()
    entry = {"path": "file.txt", "size": size, "sha": sha, "type": "blob"}
    cfg = {"owner": "o", "repo": "r", "chunk": 4}
    client = OtaClient(cfg)
    client.stage_dir = str(tmp_path / ".stage")
    client.backup_dir = str(tmp_path / ".backup")
    client.ensure_dirs(client.stage_dir)
    client.ensure_dirs(client.backup_dir)
    client._get = lambda url, raw=False: RawResp(data)
    assert client.stream_and_verify(entry, "ref")
    staged = tmp_path / ".stage" / "file.txt"
    assert staged.read_bytes() == data


def test_stream_and_verify_fail(tmp_path):
    data = b"data"
    size = len(data)
    sha = "0" * 40
    entry = {"path": "bad.txt", "size": size, "sha": sha, "type": "blob"}
    cfg = {"owner": "o", "repo": "r"}
    client = OtaClient(cfg)
    client.stage_dir = str(tmp_path / ".s")
    client.backup_dir = str(tmp_path / ".b")
    client.ensure_dirs(client.stage_dir)
    client.ensure_dirs(client.backup_dir)
    client._get = lambda url, raw=False: Resp(data)
    import pytest
    with pytest.raises(Exception):
        client.stream_and_verify(entry, "ref")
