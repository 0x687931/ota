import os
from ota import OTA


def test_client_startup_rollback(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs('.ota_backup', exist_ok=True)
    os.makedirs('.ota_stage', exist_ok=True)
    with open('.ota_backup/app.txt', 'w') as f:
        f.write('old')
    with open('.ota_stage/app.txt', 'w') as f:
        f.write('new')
    OTA({'owner': 'o', 'repo': 'r'})
    assert (tmp_path / 'app.txt').read_text() == 'old'
    assert os.listdir('.ota_backup') == []
    assert os.listdir('.ota_stage') == []
