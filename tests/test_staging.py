import os
import json
from ota_updater import OTAUpdater, sha256_file


def test_staging_and_swap(tmp_path):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        # Existing file
        with open('app.py', 'w') as f:
            f.write('old')
        updater = OTAUpdater({}, log=False)
        # Prepare staged file
        staged = os.path.join(updater.stage_dir, 'app.py')
        os.makedirs(os.path.dirname(staged), exist_ok=True)
        with open(staged, 'w') as f:
            f.write('new')
        manifest = {
            'version': 'v1',
            'files': [{
                'path': 'app.py',
                'sha256': sha256_file(staged),
                'size': os.path.getsize(staged)
            }]
        }
        updater._apply_update(manifest)
        # Verify update
        with open('app.py') as f:
            assert f.read() == 'new'
        with open('version.json') as f:
            assert json.load(f)['version'] == 'v1'
    finally:
        os.chdir(cwd)
