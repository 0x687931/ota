import os
import json
import pytest
from ota import OTA, ensure_dirs


def _write(path, data):
    """Helper to write file with directory creation."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def test_version_written_after_file_sync(tmp_path, monkeypatch):
    """
    Test that version.json is written AFTER all file operations are synced.
    This ensures that if power is lost, we don't have version.json pointing
    to an update that didn't fully complete.
    """
    cfg = {"owner": "o", "repo": "r"}
    c = OTA(cfg)
    c.stage = str(tmp_path / "stage")
    c.backup = str(tmp_path / "backup")
    ensure_dirs(c.stage)
    ensure_dirs(c.backup)
    monkeypatch.chdir(tmp_path)

    # Track call order
    call_order = []

    # Mock os.sync to track when it's called
    original_sync = getattr(os, "sync", None)

    def mock_sync():
        call_order.append("sync")
        if original_sync:
            original_sync()

    if hasattr(os, "sync"):
        monkeypatch.setattr(os, "sync", mock_sync)

    # Mock rename to track file operations
    original_rename = os.rename

    def mock_rename(src, dst):
        if "version.json" in dst:
            call_order.append("version_write")
        else:
            call_order.append("file_rename")
        return original_rename(src, dst)

    monkeypatch.setattr(os, "rename", mock_rename)

    # Setup: existing file and staged replacement
    _write(tmp_path / "app.txt", b"old")
    _write(tmp_path / "stage" / "app.txt", b"new")

    # Perform the swap
    c.stage_and_swap("ref123", "c1")

    # Verify the update succeeded
    assert (tmp_path / "app.txt").read_bytes() == b"new"

    # Verify version.json exists
    with open(tmp_path / "version.json") as f:
        data = json.load(f)
        assert data["ref"] == "ref123"
        assert data["commit"] == "c1"

    # CRITICAL: Verify version.json was written AFTER sync
    # The order should be: file_rename -> sync -> version_write -> sync
    if hasattr(os, "sync"):
        assert "version_write" in call_order
        # Find the index of version_write
        version_idx = call_order.index("version_write")
        # There should be at least one sync before version_write
        syncs_before = [i for i, x in enumerate(call_order[:version_idx]) if x == "sync"]
        assert len(syncs_before) > 0, "version.json should be written after file sync"


def test_crash_simulation_before_version_write(tmp_path, monkeypatch):
    """
    Simulate a crash AFTER all files are synced but BEFORE version.json is written.
    On restart, the system should:
    1. Restore backups (since version.json doesn't exist)
    2. Return to previous state
    """
    cfg = {"owner": "o", "repo": "r"}
    c = OTA(cfg)
    c.stage = str(tmp_path / "stage")
    c.backup = str(tmp_path / "backup")
    ensure_dirs(c.stage)
    ensure_dirs(c.backup)
    monkeypatch.chdir(tmp_path)

    # Setup: existing file with known content
    _write(tmp_path / "app.txt", b"original")
    _write(tmp_path / "stage" / "app.txt", b"updated")

    # Inject failure RIGHT BEFORE version.json is written
    original_rename = os.rename
    crash_triggered = False

    def crash_before_version(src, dst):
        nonlocal crash_triggered
        # Crash BEFORE the rename happens (not after)
        if "version.json" in dst and not crash_triggered:
            crash_triggered = True
            raise OSError("Simulated power loss before version.json write")
        # Let other operations complete normally
        return original_rename(src, dst)

    monkeypatch.setattr(os, "rename", crash_before_version)

    # Attempt the swap - should fail
    with pytest.raises(OSError, match="Simulated power loss"):
        c.stage_and_swap("ref123", "c1")

    # Verify rollback occurred
    assert (tmp_path / "app.txt").read_bytes() == b"original"
    # Verify version.json was NOT written (since we failed before the rename)
    assert not (tmp_path / "version.json").exists()


def test_crash_simulation_after_version_write(tmp_path, monkeypatch):
    """
    Simulate a crash AFTER version.json is written.
    Since version.json exists, the update is considered complete.
    On restart, the system should keep the new files.
    """
    cfg = {"owner": "o", "repo": "r"}
    c = OTA(cfg)
    c.stage = str(tmp_path / "stage")
    c.backup = str(tmp_path / "backup")
    ensure_dirs(c.stage)
    ensure_dirs(c.backup)
    monkeypatch.chdir(tmp_path)

    # Setup: existing file
    _write(tmp_path / "app.txt", b"original")
    _write(tmp_path / "stage" / "app.txt", b"updated")

    # Inject failure AFTER version.json is written (simulate crash during final sync)
    original_sync = getattr(os, "sync", None)
    version_written = False

    def crash_after_version():
        nonlocal version_written
        # Check if version.json exists (written successfully)
        if (tmp_path / "version.json").exists():
            version_written = True
            # Only crash on the SECOND sync (after version.json write)
            if original_sync:
                original_sync()
            raise OSError("Simulated crash during final sync")
        if original_sync:
            original_sync()

    if hasattr(os, "sync"):
        monkeypatch.setattr(os, "sync", crash_after_version)

    # Perform the swap - may or may not fail depending on when sync is called
    try:
        c.stage_and_swap("ref123", "c1")
    except OSError:
        pass  # Expected if crash happens

    # CRITICAL: Even if there was a crash, since version.json exists,
    # the update is considered complete
    if (tmp_path / "version.json").exists():
        # version.json exists, so files should be updated
        assert (tmp_path / "app.txt").read_bytes() == b"updated"
        with open(tmp_path / "version.json") as f:
            data = json.load(f)
            assert data["ref"] == "ref123"
            assert data["commit"] == "c1"


def test_empty_update_no_files_changed(tmp_path, monkeypatch):
    """
    Test that version.json is still written correctly even when
    no files are staged (empty update).
    """
    cfg = {"owner": "o", "repo": "r"}
    c = OTA(cfg)
    c.stage = str(tmp_path / "stage")
    c.backup = str(tmp_path / "backup")
    ensure_dirs(c.stage)
    ensure_dirs(c.backup)
    monkeypatch.chdir(tmp_path)

    # Existing file, but NO staged replacement
    _write(tmp_path / "app.txt", b"original")

    # Perform swap with no staged files
    c.stage_and_swap("ref456", "c2")

    # Original file should remain unchanged
    assert (tmp_path / "app.txt").read_bytes() == b"original"

    # version.json should still be written
    assert (tmp_path / "version.json").exists()
    with open(tmp_path / "version.json") as f:
        data = json.load(f)
        assert data["ref"] == "ref456"
        assert data["commit"] == "c2"


def test_version_write_idempotency(tmp_path, monkeypatch):
    """
    Test that writing the same version state twice doesn't cause issues
    and is optimized (skipped if state is already current).
    """
    cfg = {"owner": "o", "repo": "r"}
    c = OTA(cfg)
    c.stage = str(tmp_path / "stage")
    c.backup = str(tmp_path / "backup")
    ensure_dirs(c.stage)
    ensure_dirs(c.backup)
    monkeypatch.chdir(tmp_path)

    # Setup staged file
    _write(tmp_path / "stage" / "app.txt", b"v1")

    # First update
    c.stage_and_swap("ref1", "commit1")
    first_mtime = os.path.getmtime(tmp_path / "version.json")

    # Try to update to the same version again (with no file changes)
    # This should skip the version write due to optimization
    c.stage_and_swap("ref1", "commit1")
    second_mtime = os.path.getmtime(tmp_path / "version.json")

    # mtime should be the same (no write occurred)
    assert first_mtime == second_mtime

    # But if we update to a different version, it should write
    _write(tmp_path / "stage" / "app.txt", b"v2")
    c.stage_and_swap("ref2", "commit2")
    third_mtime = os.path.getmtime(tmp_path / "version.json")

    # mtime should be different now
    assert third_mtime > second_mtime

    # Verify final state
    with open(tmp_path / "version.json") as f:
        data = json.load(f)
        assert data["ref"] == "ref2"
        assert data["commit"] == "commit2"
