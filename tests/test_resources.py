from ota import OTA


def test_update_aborts_when_storage_low(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ota = OTA({"owner": "x", "repo": "y"})
    monkeypatch.setattr(ota, "connect", lambda: None)
    monkeypatch.setattr(ota, "_check_basic_resources", lambda: True)
    monkeypatch.setattr(ota, "resolve_target", lambda: {"mode": "branch", "ref": "r", "commit": "c"})
    monkeypatch.setattr(ota, "fetch_tree", lambda commit: [{"path": "app.txt", "type": "blob", "size": 10}])

    called = {}

    def fake_check_storage(required):
        called["required"] = required
        return False

    monkeypatch.setattr(ota, "_check_storage", fake_check_storage)
    assert ota.update_if_available() is False
    assert called["required"] == 20

