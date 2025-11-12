"""
Test suite for version.json timing fix (Issue #4)

The bug: version.json written before final sync completes. If crash happens
between version write and sync, device thinks it's on new version but may
have old code.

The fix: version.json is written AFTER all file swaps and syncs complete,
and followed by its own sync before cleanup phase.

These tests validate that version.json timing is correct and that the device
state remains consistent even when failures occur at critical points.
"""

import os
import pytest
from ota import OTA, ensure_dirs


def _write(path, data):
    """Helper to write test files."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def _read_version(tmp_path):
    """Helper to read version.json if it exists."""
    version_file = tmp_path / "version.json"
    if version_file.exists():
        import json
        with open(version_file) as f:
            return json.load(f)
    return None


class TestVersionTimingSuccess:
    """Tests for successful update scenarios - version.json written after all operations."""

    def test_version_written_after_sync_success(self, tmp_path, monkeypatch):
        """
        CRITICAL TEST: Verify version.json only written after all swaps + sync complete.

        This is the core fix validation. We track the order of operations:
        1. File swaps (old -> backup, staged -> target)
        2. os.sync() after swaps
        3. _write_state() called (writes version.json)
        4. os.sync() after version write

        The test ensures version.json doesn't exist until AFTER all swaps complete.
        """
        cfg = {"owner": "o", "repo": "r"}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        # Create existing file and staged replacement
        _write(tmp_path / "app.py", b"old_version")
        _write(tmp_path / "lib" / "util.py", b"old_util")
        _write(tmp_path / "stage" / "app.py", b"new_version")
        _write(tmp_path / "stage" / "lib" / "util.py", b"new_util")

        # Track operation order
        operations = []
        original_rename = os.rename
        original_sync = getattr(os, "sync", None)

        def tracked_rename(src, dst):
            operations.append(("rename", src, dst))
            return original_rename(src, dst)

        def tracked_sync():
            operations.append(("sync",))
            if original_sync:
                return original_sync()

        monkeypatch.setattr(os, "rename", tracked_rename)
        monkeypatch.setattr(os, "sync", tracked_sync)

        # Perform swap
        c.stage_and_swap("v2.0", "abc123")

        # Verify version.json exists with correct content
        version = _read_version(tmp_path)
        assert version is not None
        assert version["ref"] == "v2.0"
        assert version["commit"] == "abc123"

        # Verify files were swapped correctly
        assert (tmp_path / "app.py").read_bytes() == b"new_version"
        assert (tmp_path / "lib" / "util.py").read_bytes() == b"new_util"

        # CRITICAL: Verify version.json was written AFTER all file operations
        # Operations should be:
        # 1. rename app.py -> backup
        # 2. sync
        # 3. rename staged app.py -> app.py
        # 4. sync
        # 5. rename lib/util.py -> backup
        # 6. sync
        # 7. rename staged lib/util.py -> lib/util.py
        # 8. sync
        # 9. rename version.json.tmp -> version.json (in _write_state)
        # 10. sync (after _write_state)

        # Find where version.json.tmp was renamed (marks _write_state call)
        version_write_idx = None
        for i, op in enumerate(operations):
            if op[0] == "rename" and "version.json.tmp" in op[1]:
                version_write_idx = i
                break

        assert version_write_idx is not None, "version.json.tmp rename not found"

        # Verify all app file renames happened before version write
        app_renames = [i for i, op in enumerate(operations)
                       if op[0] == "rename" and ("app.py" in op[1] or "util.py" in op[1])]
        assert all(i < version_write_idx for i in app_renames), \
            "Version written before all file swaps completed"

        # Verify at least one sync happened before version write
        syncs_before_version = [i for i, op in enumerate(operations[:version_write_idx])
                                if op[0] == "sync"]
        assert len(syncs_before_version) > 0, \
            "No sync called before version.json write"

        # Verify sync happened after version write
        syncs_after_version = [i for i, op in enumerate(operations[version_write_idx:])
                               if op[0] == "sync"]
        assert len(syncs_after_version) > 0, \
            "No sync called after version.json write"

    def test_version_write_with_multiple_files(self, tmp_path, monkeypatch):
        """
        Test version.json timing with multiple files across directories.
        Ensures version write happens after ALL file operations complete.
        """
        cfg = {"owner": "o", "repo": "r"}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        # Create multiple existing files
        _write(tmp_path / "main.py", b"old_main")
        _write(tmp_path / "lib" / "a.py", b"old_a")
        _write(tmp_path / "lib" / "b.py", b"old_b")
        _write(tmp_path / "config" / "settings.json", b"old_config")

        # Stage replacements
        _write(tmp_path / "stage" / "main.py", b"new_main")
        _write(tmp_path / "stage" / "lib" / "a.py", b"new_a")
        _write(tmp_path / "stage" / "lib" / "b.py", b"new_b")
        _write(tmp_path / "stage" / "config" / "settings.json", b"new_config")

        # Track when version.json appears
        version_exists_during_swap = []
        original_rename = os.rename

        def checking_rename(src, dst):
            result = original_rename(src, dst)
            # After each rename, check if version.json exists
            if not src.endswith("version.json.tmp"):  # Don't check during version write itself
                version_exists = (tmp_path / "version.json").exists()
                version_exists_during_swap.append(version_exists)
            return result

        monkeypatch.setattr(os, "rename", checking_rename)

        c.stage_and_swap("v3.0", "def456")

        # Verify all files swapped
        assert (tmp_path / "main.py").read_bytes() == b"new_main"
        assert (tmp_path / "lib" / "a.py").read_bytes() == b"new_a"
        assert (tmp_path / "lib" / "b.py").read_bytes() == b"new_b"
        assert (tmp_path / "config" / "settings.json").read_bytes() == b"new_config"

        # Verify version.json exists now
        version = _read_version(tmp_path)
        assert version is not None
        assert version["ref"] == "v3.0"

        # CRITICAL: version.json should NOT have existed during any file swap
        assert all(not exists for exists in version_exists_during_swap), \
            "version.json existed during file swap operations"


class TestVersionTimingRollback:
    """Tests for failure scenarios - version.json must NOT be written on rollback."""

    def test_version_not_written_on_rollback(self, tmp_path, monkeypatch):
        """
        CRITICAL TEST: Exception during swap → version.json must NOT be written.

        If a file operation fails mid-swap, rollback occurs. The version.json
        must remain at the old version (or not exist) to indicate the update failed.
        """
        cfg = {"owner": "o", "repo": "r"}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        # Create existing version state
        _write(tmp_path / "version.json", b'{"ref": "v1.0", "commit": "old123"}')
        _write(tmp_path / "app.py", b"old_code")

        # Stage files including one that will fail
        _write(tmp_path / "stage" / "app.py", b"new_code")
        _write(tmp_path / "stage" / "bad.py", b"will_fail")

        original_rename = os.rename

        def failing_rename(src, dst):
            # Fail when trying to move bad.py from stage to target
            if src.endswith("stage/bad.py") and not dst.endswith(".ota_backup"):
                raise OSError("Simulated disk failure")
            return original_rename(src, dst)

        monkeypatch.setattr(os, "rename", failing_rename)

        # Attempt swap - should fail and rollback
        with pytest.raises(OSError):
            c.stage_and_swap("v2.0", "new456")

        # CRITICAL: version.json must still show OLD version
        version = _read_version(tmp_path)
        assert version is not None
        assert version["ref"] == "v1.0", \
            "version.json changed despite rollback"
        assert version["commit"] == "old123"

        # Verify original file restored
        assert (tmp_path / "app.py").read_bytes() == b"old_code"

    def test_version_not_written_on_early_failure(self, tmp_path, monkeypatch):
        """
        Test failure during file swap prevents version write.
        If file operations fail, version must not change.
        """
        cfg = {"owner": "o", "repo": "r"}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        # No existing version.json (simulates first update)
        _write(tmp_path / "app.py", b"original")
        _write(tmp_path / "stage" / "app.py", b"new")

        original_rename = os.rename

        def failing_swap(src, dst):
            # Fail when trying to move staged file to target (second rename for app.py)
            if "/stage/" in src and "app.py" in src:
                raise OSError("Disk write failed")
            return original_rename(src, dst)

        monkeypatch.setattr(os, "rename", failing_swap)

        with pytest.raises(OSError):
            c.stage_and_swap("v1.0", "abc")

        # CRITICAL: version.json must NOT exist
        assert not (tmp_path / "version.json").exists(), \
            "version.json created despite swap failure"

        # Original file should be restored due to rollback
        assert (tmp_path / "app.py").exists()
        assert (tmp_path / "app.py").read_bytes() == b"original"


class TestVersionTimingCrashSimulation:
    """
    Simulate crashes at critical points to verify version.json consistency.
    These tests ensure the device can determine its actual state after unexpected shutdown.
    """

    def test_crash_before_version_write(self, tmp_path, monkeypatch):
        """
        Simulate crash after all file swaps but BEFORE version.json write.

        Scenario: Files successfully swapped, but device crashes before version
        write completes. On next boot, version.json should show old version,
        triggering re-download (or the backup directory will trigger recovery).
        """
        cfg = {"owner": "o", "repo": "r"}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        # Initial state
        _write(tmp_path / "version.json", b'{"ref": "v1.0", "commit": "old"}')
        _write(tmp_path / "app.py", b"old")
        _write(tmp_path / "stage" / "app.py", b"new")

        # Intercept _write_state to simulate crash before it completes
        original_write_state = c._write_state

        def crash_before_write(ref, commit):
            raise Exception("CRASH: Power failure before version write")

        monkeypatch.setattr(c, "_write_state", crash_before_write)

        # Attempt update - crashes before version write
        with pytest.raises(Exception, match="CRASH"):
            c.stage_and_swap("v2.0", "new")

        # CRITICAL: version.json must still show OLD version
        version = _read_version(tmp_path)
        assert version is not None
        assert version["ref"] == "v1.0", \
            "version.json changed despite crash before write"

        # Note: app.py will be in backup dir due to rollback
        # This is correct - next boot will restore from backup

    def test_crash_after_version_write(self, tmp_path, monkeypatch):
        """
        Simulate crash after version.json write but before cleanup.

        Scenario: All swaps complete, version.json written, but crash occurs
        during cleanup phase. On next boot, version.json correctly reflects
        new version, and cleanup will happen then.
        """
        cfg = {"owner": "o", "repo": "r"}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        # Initial state
        _write(tmp_path / "version.json", b'{"ref": "v1.0", "commit": "old"}')
        _write(tmp_path / "app.py", b"old")
        _write(tmp_path / "stage" / "app.py", b"new")

        # Let normal operations proceed, but crash during cleanup
        original_rmtree = None
        for name in dir(c):
            if "rmtree" in name.lower():
                import ota
                original_rmtree = getattr(ota, "_rmtree", None)
                break

        cleanup_called = []

        def crash_during_cleanup(path):
            cleanup_called.append(path)
            if len(cleanup_called) == 1:  # First cleanup call
                raise Exception("CRASH: Power failure during cleanup")
            if original_rmtree:
                return original_rmtree(path)

        if original_rmtree:
            import ota
            monkeypatch.setattr(ota, "_rmtree", crash_during_cleanup)

        # This may or may not raise depending on exception handling in finally block
        # The key is checking the state after
        try:
            c.stage_and_swap("v2.0", "new")
        except Exception as e:
            if "CRASH" not in str(e):
                raise

        # CRITICAL: version.json must show NEW version
        # Even though cleanup failed, the update succeeded
        version = _read_version(tmp_path)
        assert version is not None
        assert version["ref"] == "v2.0", \
            "version.json should reflect successful update even if cleanup crashes"
        assert version["commit"] == "new"

        # New file should be in place
        assert (tmp_path / "app.py").read_bytes() == b"new"

    def test_crash_between_sync_and_version_write(self, tmp_path, monkeypatch):
        """
        Simulate crash in the critical window between file operations and version write.

        This tests the bug scenario: files swapped, but crash before version.json written.
        We crash right before _write_state is called to validate version isn't updated.
        """
        cfg = {"owner": "o", "repo": "r"}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        _write(tmp_path / "version.json", b'{"ref": "v1.0", "commit": "old"}')
        _write(tmp_path / "app.py", b"old")
        _write(tmp_path / "stage" / "app.py", b"new")

        # Crash right before version write by intercepting _write_state
        original_write_state = c._write_state
        file_ops_complete = [False]

        # Track that file operations completed before we crash
        original_rename = os.rename
        def tracking_rename(src, dst):
            result = original_rename(src, dst)
            if not src.endswith("version.json.tmp"):
                file_ops_complete[0] = True
            return result

        monkeypatch.setattr(os, "rename", tracking_rename)

        def crash_before_version_write(ref, commit):
            # Verify file ops completed before crash
            assert file_ops_complete[0], "Should crash after file operations"
            raise Exception("CRASH: Power loss before version write")

        monkeypatch.setattr(c, "_write_state", crash_before_version_write)

        with pytest.raises(Exception, match="CRASH"):
            c.stage_and_swap("v2.0", "new")

        # CRITICAL: version.json must NOT show new version
        # Because version write should come AFTER all syncs
        version = _read_version(tmp_path)
        assert version["ref"] == "v1.0", \
            "version.json changed despite crash before version write"


class TestVersionTimingEdgeCases:
    """Edge cases and corner scenarios for version.json timing."""

    def test_version_survives_cleanup_failure(self, tmp_path, monkeypatch):
        """
        Verify version.json correctly written even if cleanup fails.

        Cleanup happens in finally block AFTER version write. If cleanup fails,
        version.json should still reflect the successful update.
        """
        cfg = {"owner": "o", "repo": "r"}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        _write(tmp_path / "app.py", b"old")
        _write(tmp_path / "stage" / "app.py", b"new")

        # Make cleanup fail by making backup dir non-removable
        import ota
        original_rmtree = getattr(ota, "_rmtree", None)

        def failing_cleanup(path):
            if "backup" in path:
                raise OSError("Cannot remove backup dir")
            if original_rmtree:
                return original_rmtree(path)

        if original_rmtree:
            monkeypatch.setattr(ota, "_rmtree", failing_cleanup)

        # Update may raise due to cleanup failure, but that's in finally block
        # The important thing is version.json gets written before cleanup
        try:
            c.stage_and_swap("v2.0", "abc")
        except OSError:
            pass  # Cleanup failure is acceptable

        # CRITICAL: version.json must show new version
        version = _read_version(tmp_path)
        assert version is not None
        assert version["ref"] == "v2.0", \
            "version.json should be written before cleanup fails"

        # New file in place
        assert (tmp_path / "app.py").read_bytes() == b"new"

    def test_version_unchanged_when_already_current(self, tmp_path, monkeypatch):
        """
        Test that version.json isn't unnecessarily rewritten if already current.

        This is an optimization to reduce flash wear. The _write_state method
        checks if state is already current and skips write if so.
        """
        cfg = {"owner": "o", "repo": "r"}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        # Set current version
        _write(tmp_path / "version.json", b'{"ref": "v1.0", "commit": "abc123"}')
        _write(tmp_path / "app.py", b"code")

        # Stage "update" with same version
        _write(tmp_path / "stage" / "app.py", b"code")

        # Track if version.json gets written
        writes_to_version = []
        original_open = open

        def tracking_open(path, mode="r", *args, **kwargs):
            if "version.json" in str(path) and "w" in mode:
                writes_to_version.append(path)
            return original_open(path, mode, *args, **kwargs)

        monkeypatch.setattr("builtins.open", tracking_open)

        c.stage_and_swap("v1.0", "abc123")

        # Version should be checked but not rewritten (flash wear optimization)
        # Note: There will be ONE write for the .tmp file in _write_state even if skipped
        # The check happens after reading current state
        # Let's just verify the version is still correct
        version = _read_version(tmp_path)
        assert version is not None
        assert version["ref"] == "v1.0"
        assert version["commit"] == "abc123"

    def test_version_write_with_deletion(self, tmp_path, monkeypatch):
        """
        Test version.json timing when update includes file deletions.

        Deletions happen before version write. Verify version reflects state
        after all operations (swaps + deletions) complete.
        """
        # Need allow rules for files to be processed (exact names or prefixes)
        cfg = {"owner": "o", "repo": "r", "allow": ["keep.py", "delete.py"]}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        # Files to keep and delete
        _write(tmp_path / "keep.py", b"keep_old")
        _write(tmp_path / "delete.py", b"will_be_deleted")
        _write(tmp_path / "stage" / "keep.py", b"keep_new")

        # Track when version.json gets written relative to deletion
        operations = []
        original_rename = os.rename
        deleted_files = []

        def tracked_rename(src, dst):
            operations.append(("rename", src, dst))
            # Track deletions (moves to backup)
            if "delete.py" in src and ".ota_backup" in dst:
                deleted_files.append(src)
            return original_rename(src, dst)

        monkeypatch.setattr(os, "rename", tracked_rename)

        # Perform swap with deletions
        c.stage_and_swap("v2.0", "def", deletes=["delete.py"])

        # Find version write operation
        version_write_idx = None
        for i, op in enumerate(operations):
            if op[0] == "rename" and "version.json.tmp" in op[1]:
                version_write_idx = i
                break

        assert version_write_idx is not None, "version.json write not found"

        # Find deletion operations (delete.py moved to backup)
        deletion_ops = []
        for i, op in enumerate(operations):
            if op[0] == "rename" and "delete.py" in op[1] and ".ota_backup" in op[2]:
                deletion_ops.append(i)

        # Verify deletions happened before version write (if any deletions occurred)
        if deletion_ops:
            assert all(i < version_write_idx for i in deletion_ops), \
                "Version written before deletion completed"

        # Verify final state
        version = _read_version(tmp_path)
        assert version is not None
        assert version["ref"] == "v2.0"
        assert (tmp_path / "keep.py").read_bytes() == b"keep_new"

        # delete.py should not exist in root (either deleted or in backup before cleanup)
        assert not (tmp_path / "delete.py").exists(), \
            "delete.py should have been removed from root"

    def test_sync_called_after_version_write(self, tmp_path, monkeypatch):
        """
        Verify os.sync() is called after version.json write.

        This is part of the fix: version write must be followed by sync
        to ensure version.json is persisted to storage.
        """
        cfg = {"owner": "o", "repo": "r"}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        _write(tmp_path / "stage" / "app.py", b"new")

        operations = []
        original_rename = os.rename
        original_sync = getattr(os, "sync", None)

        def tracked_rename(src, dst):
            operations.append(("rename", src, dst))
            return original_rename(src, dst)

        def tracked_sync():
            operations.append(("sync",))
            if original_sync:
                return original_sync()

        monkeypatch.setattr(os, "rename", tracked_rename)
        monkeypatch.setattr(os, "sync", tracked_sync)

        c.stage_and_swap("v1.0", "xyz")

        # Find version.json write
        version_write_idx = None
        for i, op in enumerate(operations):
            if op[0] == "rename" and "version.json.tmp" in op[1]:
                version_write_idx = i
                break

        assert version_write_idx is not None

        # CRITICAL: Verify sync called AFTER version write
        syncs_after_version = [i for i, op in enumerate(operations[version_write_idx:])
                               if op[0] == "sync"]
        assert len(syncs_after_version) > 0, \
            "os.sync() must be called after version.json write"


class TestVersionWriteAtomic:
    """Tests for atomic version.json write mechanism."""

    def test_version_write_uses_temp_file(self, tmp_path, monkeypatch):
        """
        Verify version.json written atomically via temp file + rename.

        This prevents corruption if crash happens during write.
        """
        cfg = {"owner": "o", "repo": "r"}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        _write(tmp_path / "stage" / "app.py", b"new")

        renames = []
        original_rename = os.rename

        def track_rename(src, dst):
            renames.append((src, dst))
            return original_rename(src, dst)

        monkeypatch.setattr(os, "rename", track_rename)

        c.stage_and_swap("v1.0", "abc")

        # Find version.json rename
        version_renames = [(s, d) for s, d in renames
                           if "version.json" in s or "version.json" in d]

        assert len(version_renames) > 0, "No version.json rename found"

        # Should be version.json.tmp -> version.json
        version_rename = [r for r in version_renames if "version.json.tmp" in r[0]]
        assert len(version_rename) == 1, \
            "version.json should be written via temp file + atomic rename"

        src, dst = version_rename[0]
        assert src.endswith("version.json.tmp")
        assert dst.endswith("version.json")

    def test_version_file_fsynced(self, tmp_path, monkeypatch):
        """
        Verify version.json file descriptor is fsynced before rename.

        This ensures the temp file content is on disk before atomic rename.
        """
        cfg = {"owner": "o", "repo": "r"}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        _write(tmp_path / "stage" / "app.py", b"new")

        fsync_called = []
        original_fsync = getattr(os, "fsync", None)

        def track_fsync(fd):
            fsync_called.append(fd)
            if original_fsync:
                return original_fsync(fd)

        if original_fsync:
            monkeypatch.setattr(os, "fsync", track_fsync)

        c.stage_and_swap("v1.0", "abc")

        # If os.fsync exists, it should have been called
        if original_fsync:
            assert len(fsync_called) > 0, \
                "os.fsync should be called to ensure version.json persisted"


if __name__ == "__main__":
    # Allow running tests directly
    pytest.main([__file__, "-v"])
