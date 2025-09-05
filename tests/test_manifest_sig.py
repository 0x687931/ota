import json
import hmac
import hashlib
import pytest
from ota_updater import OTAUpdater, OTAError


def _sign(manifest, key):
    data = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    sig = hmac.new(key.encode(), data, hashlib.sha256).hexdigest()
    manifest['signature'] = sig
    return manifest


def test_manifest_signature_ok():
    m = _sign({'version': '1', 'files': []}, 'secret')
    upd = OTAUpdater({'manifest_key': 'secret'}, log=False)
    upd._verify_manifest_signature(m)


def test_manifest_signature_bad():
    m = {'version': '1', 'files': [], 'signature': 'bad'}
    upd = OTAUpdater({'manifest_key': 'secret'}, log=False)
    with pytest.raises(OTAError):
        upd._verify_manifest_signature(m)
