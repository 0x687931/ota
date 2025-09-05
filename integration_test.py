"""Minimal integration test for OTAUpdater.

This script simulates applying an update using a local manifest and
staged files.  It is intended for manual execution on a development
machine and does not perform any network operations."""

import json
import os
from ota_updater import OTAUpdater, sha256_file


def main():
    # prepare a staged file
    updater = OTAUpdater({}, log=True)
    os.makedirs(updater.stage_dir, exist_ok=True)
    with open(os.path.join(updater.stage_dir, 'example.txt'), 'w') as f:
        f.write('demo')
    manifest = {
        'version': 'v0-test',
        'files': [{
            'path': 'example.txt',
            'sha256': sha256_file(os.path.join(updater.stage_dir, 'example.txt')),
            'size': os.path.getsize(os.path.join(updater.stage_dir, 'example.txt')),
        }]
    }
    # write manifest for inspection
    with open('manifest.json', 'w') as f:
        json.dump(manifest, f, indent=2)
    updater._apply_update(manifest)
    print('Update applied. Contents:', open('example.txt').read())


if __name__ == '__main__':
    main()
