import os
import hashlib
import io
from ota import OTA, git_blob_sha1_stream, ensure_dirs


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
    client = OTA(cfg)
    client.stage = str(tmp_path / ".stage")
    client.backup = str(tmp_path / ".backup")
    ensure_dirs(client.stage)
    ensure_dirs(client.backup)
    client._get = lambda url, raw=False: Resp(data)
    client.stream_and_verify_git(entry, "ref")
    staged = tmp_path / ".stage" / "file.txt"
    assert staged.read_bytes() == data


def test_stream_and_verify_requests_raw(tmp_path):
    data = b"hello"
    size = len(data)
    sha = hashlib.sha1(b"blob " + str(size).encode() + b"\0" + data).hexdigest()
    entry = {"path": "file.txt", "size": size, "sha": sha, "type": "blob"}
    cfg = {"owner": "o", "repo": "r", "chunk": 4}
    client = OTA(cfg)
    client.stage = str(tmp_path / ".stage")
    client.backup = str(tmp_path / ".backup")
    ensure_dirs(client.stage)
    ensure_dirs(client.backup)
    client._get = lambda url, raw=False: RawResp(data)
    client.stream_and_verify_git(entry, "ref")
    staged = tmp_path / ".stage" / "file.txt"
    assert staged.read_bytes() == data


def test_stream_and_verify_fail(tmp_path):
    data = b"data"
    size = len(data)
    sha = "0" * 40
    entry = {"path": "bad.txt", "size": size, "sha": sha, "type": "blob"}
    cfg = {"owner": "o", "repo": "r"}
    client = OTA(cfg)
    client.stage = str(tmp_path / ".s")
    client.backup = str(tmp_path / ".b")
    ensure_dirs(client.stage)
    ensure_dirs(client.backup)
    client._get = lambda url, raw=False: Resp(data)
    import pytest
    with pytest.raises(Exception):
        client.stream_and_verify_git(entry, "ref")
