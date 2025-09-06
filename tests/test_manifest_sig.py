import json
import hmac
import hashlib
import pytest
from ota import OTA, OTAError


def _sign(manifest, key):
    data = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    sig = hmac.new(key.encode(), data, hashlib.sha256).hexdigest()
    manifest['signature'] = sig
    return manifest


def test_manifest_signature_ok():
    m = _sign({'version': '1', 'files': []}, 'secret')
    upd = OTA({'manifest_key': 'secret'})
    upd._verify_manifest_signature(m)


def test_manifest_signature_bad():
    m = {'version': '1', 'files': [], 'signature': 'bad'}
    upd = OTA({'manifest_key': 'secret'})
    with pytest.raises(OTAError):
        upd._verify_manifest_signature(m)
