import os
from ota import OTA


def test_force_update_triggers(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cfg = {
        'owner': 'o',
        'repo': 'r',
        'allow': [],
        'force': True,
    }
    ota = OTA(cfg)
    monkeypatch.setattr(ota, 'connect', lambda: None)
    monkeypatch.setattr(ota, '_check_basic_resources', lambda: True)
    monkeypatch.setattr(ota, '_debug_resources', lambda: None)
    monkeypatch.setattr(ota, 'resolve_target', lambda: {'mode': 'branch', 'ref': 'main', 'commit': 'abc'})
    monkeypatch.setattr(ota, '_read_state', lambda: {'commit': 'abc'})
    monkeypatch.setattr(ota, 'fetch_tree', lambda commit: [])
    monkeypatch.setattr(ota, '_check_storage', lambda required: True)
    monkeypatch.setattr(ota, 'iter_candidates', lambda tree: iter([]))
    stage_called = {}
    monkeypatch.setattr(ota, 'stage_and_swap', lambda ref, commit, **_: stage_called.setdefault('called', True))
    monkeypatch.setattr(ota, '_perform_reset', lambda: stage_called.setdefault('reset', True))
    assert ota.update_if_available()
    assert stage_called.get('called')
    assert stage_called.get('reset')
