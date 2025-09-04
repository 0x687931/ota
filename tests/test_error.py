import os
import pytest
from ota_updater import OTAUpdater, sha256_file


def test_rollback_on_failure(tmp_path):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        with open('app.py', 'w') as f:
            f.write('old')
        updater = OTAUpdater({}, log=False)
        staged = os.path.join(updater.stage_dir, 'app.py')
        os.makedirs(os.path.dirname(staged), exist_ok=True)
        with open(staged, 'w') as f:
            f.write('new')
        manifest = {
            'version': 'v1',
            'files': [
                {'path': 'app.py', 'sha256': sha256_file(staged), 'size': os.path.getsize(staged)},
                {'path': 'missing.py', 'sha256': '0'*64, 'size': 1},
            ]
        }
        with pytest.raises(Exception):
            updater._apply_update(manifest)
        with open('app.py') as f:
            assert f.read() == 'old'
        assert not os.path.exists('missing.py')
    finally:
        os.chdir(cwd)
