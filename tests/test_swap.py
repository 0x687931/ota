import os
import pytest
from ota import OTA, ensure_dirs


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def test_stage_and_swap(tmp_path, monkeypatch):
    cfg = {"owner": "o", "repo": "r"}
    c = OTA(cfg)
    c.stage = str(tmp_path / "stage")
    c.backup = str(tmp_path / "backup")
    ensure_dirs(c.stage)
    ensure_dirs(c.backup)
    monkeypatch.chdir(tmp_path)
    # existing file
    _write(tmp_path / "app.txt", b"old")
    # staged replacement
    _write(tmp_path / "stage" / "app.txt", b"new")
    c.stage_and_swap("ref123", "c1")
    assert (tmp_path / "app.txt").read_bytes() == b"new"
    with open(tmp_path / "version.json") as f:
        assert f.read().strip() == '{"ref": "ref123", "commit": "c1"}'


def test_stage_and_swap_rollback(tmp_path, monkeypatch):
    cfg = {"owner": "o", "repo": "r"}
    c = OTA(cfg)
    c.stage = str(tmp_path / "stage")
    c.backup = str(tmp_path / "backup")
    ensure_dirs(c.stage)
    ensure_dirs(c.backup)
    monkeypatch.chdir(tmp_path)
    _write(tmp_path / "app.txt", b"orig")
    _write(tmp_path / "stage" / "app.txt", b"new")
    _write(tmp_path / "stage" / "bad.txt", b"boom")

    orig_rename = os.rename

    def failing(src, dst):
        if src.endswith("bad.txt"):
            raise OSError("fail")
        return orig_rename(src, dst)

    monkeypatch.setattr(os, "rename", failing)
    with pytest.raises(OSError):
        c.stage_and_swap("ref", "commit")
    # original file restored
    assert (tmp_path / "app.txt").read_bytes() == b"orig"
    assert not (tmp_path / "version.json").exists()
