import os
import tempfile
import hashlib
import binascii

from ota import sha256_file, crc32_file, git_blob_sha1_stream


def test_sha256_and_crc32():
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"hello world")
        name = f.name
    try:
        assert sha256_file(name) == hashlib.sha256(b"hello world").hexdigest()
        assert crc32_file(name) == (binascii.crc32(b"hello world") & 0xFFFFFFFF)
    finally:
        os.remove(name)


def test_git_blob_sha1_stream():
    data = b"blob data"
    size = len(data)

    def reader(n):
        yield data

    expect = hashlib.sha1(b"blob " + str(size).encode() + b"\x00" + data).hexdigest()
    assert git_blob_sha1_stream(size, reader, 1024) == expect
