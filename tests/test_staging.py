import os
import json
from ota import OTA, ensure_dirs


def test_staging_and_swap(tmp_path):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        # Existing file
        with open('app.py', 'w') as f:
            f.write('old')
        updater = OTA({})
        # Prepare staged file
        staged = os.path.join(updater.stage, 'app.py')
        os.makedirs(os.path.dirname(staged), exist_ok=True)
        with open(staged, 'w') as f:
            f.write('new')
        ensure_dirs(updater.stage)
        ensure_dirs(updater.backup)
        updater.stage_and_swap('v1', 'deadbeef')
        # Verify update
        with open('app.py') as f:
            assert f.read() == 'new'
        with open('version.json') as f:
            data = json.load(f)
            assert data['ref'] == 'v1'
            assert data['commit'] == 'deadbeef'
    finally:
        os.chdir(cwd)
