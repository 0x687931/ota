import os
from ota import OTA


def test_startup_rollback(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs('.ota_backup', exist_ok=True)
    os.makedirs('.ota_stage', exist_ok=True)
    with open('.ota_backup/app.txt', 'w') as f:
        f.write('old')
    with open('.ota_stage/app.txt', 'w') as f:
        f.write('new')
    OTA({})
    assert (tmp_path / 'app.txt').read_text() == 'old'
    assert os.listdir('.ota_backup') == []
    assert os.listdir('.ota_stage') == []


def test_startup_stage_cleanup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs('.ota_stage', exist_ok=True)
    with open('.ota_stage/app.txt', 'w') as f:
        f.write('new')
    os.makedirs('.ota_backup', exist_ok=True)
    OTA({})
    assert os.listdir('.ota_stage') == []


def test_startup_custom_dirs(tmp_path, monkeypatch):
    stage = tmp_path / 'custom_stage'
    backup = tmp_path / 'custom_backup'
    os.makedirs(stage, exist_ok=True)
    os.makedirs(backup, exist_ok=True)
    with open(backup / 'app.txt', 'w') as f:
        f.write('old')
    with open(stage / 'app.txt', 'w') as f:
        f.write('new')
    monkeypatch.chdir(tmp_path)
    OTA({'stage_dir': str(stage), 'backup_dir': str(backup)})
    assert (tmp_path / 'app.txt').read_text() == 'old'
    assert list(stage.iterdir()) == []
    assert list(backup.iterdir()) == []
