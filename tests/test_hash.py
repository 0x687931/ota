import os
import tempfile
import hashlib
import binascii
import pytest

from ota_updater import OTAUpdater, sha256_file, crc32_file, OTAError


def test_sha256_and_crc32():
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"hello world")
        name = f.name
    try:
        assert sha256_file(name) == hashlib.sha256(b"hello world").hexdigest()
        assert crc32_file(name) == (binascii.crc32(b"hello world") & 0xFFFFFFFF)
    finally:
        os.remove(name)


def test_verify_file():
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"data")
        name = f.name
    upd = OTAUpdater({}, log=False)
    size = os.path.getsize(name)
    sha = hashlib.sha256(b"data").hexdigest()
    upd._verify_file(name, sha, size)
    with pytest.raises(OTAError):
        upd._verify_file(name, "0" * 64, size)
    os.remove(name)
