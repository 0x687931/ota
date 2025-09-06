from ota import OTA


def test_format_version():
    ota = OTA({})
    assert ota._format_version({'ref': 'v1.0', 'commit': 'abcdef1234567890'}) == 'v1.0 #abcdef1'
    assert ota._format_version({'ref': 'v1.0'}) == 'v1.0'
    assert ota._format_version({'commit': 'abcdef1234567890'}) == '#abcdef1'
    assert ota._format_version({}) == ''


def test_update_if_available_formats_debug(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    cfg = {'owner': 'o', 'repo': 'r', 'debug': True}
    ota = OTA(cfg)
    monkeypatch.setattr(ota, 'connect', lambda: None)
    monkeypatch.setattr(ota, '_check_basic_resources', lambda: True)
    monkeypatch.setattr(ota, '_debug_resources', lambda: None)
    target = {'mode': 'branch', 'ref': 'main', 'commit': '123456789abcdef'}
    state = {'ref': 'v1', 'commit': '9876543210abcdef'}
    monkeypatch.setattr(ota, 'resolve_target', lambda: target)
    monkeypatch.setattr(ota, '_read_state', lambda: state)
    monkeypatch.setattr(ota, 'fetch_tree', lambda commit: [])
    monkeypatch.setattr(ota, 'iter_candidates', lambda tree: iter([]))
    monkeypatch.setattr(ota, '_check_storage', lambda required: True)
    monkeypatch.setattr(ota, 'stage_and_swap', lambda ref, commit, **kwargs: None)
    monkeypatch.setattr(ota, '_perform_reset', lambda: None)
    assert ota.update_if_available()
    out = capsys.readouterr().out.splitlines()
    assert "[OTA] Resolving target: branch main #1234567" in out
    assert "[OTA] Installed version: v1 #9876543" in out
    assert "[OTA] Repo version: main #1234567" in out
