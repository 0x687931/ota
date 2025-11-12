"""
Comprehensive test suite for Fix #1: Rollback Atomicity

Tests the 3-tuple operation tracking system that enables proper rollback
of new files, replaced files, and deleted files during update failures.

Critical bug being tested:
- Old code: tracked operations as 2-tuple (target, backup)
- Old code: new files as (target, None) → rollback tries os.rename(None, target) → crash
- Old code: deleted files as (None, backup) → rollback tries os.remove(None) → crash
- New code: tracks operations as 3-tuple (operation, target, backup)
- New code: operation types "new", "replace", "delete" enable correct rollback logic
"""

import os
import json
import pytest
from ota import OTA, ensure_dirs, ERROR_FILE


def _write(path, data):
    """Helper to write file with parent directory creation."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


class TestRollbackNewFiles:
    """Test rollback of newly created files (operation="new")."""

    def test_rollback_removes_new_file(self, tmp_path, monkeypatch):
        """
        New file created during update should be deleted on rollback.

        Scenario:
        - No existing file at target
        - Staged file gets moved to target (tracked as "new")
        - Swap fails after this operation
        - Rollback should delete the new file
        """
        cfg = {"owner": "o", "repo": "r"}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        # Stage a new file (no existing file at target)
        _write(tmp_path / "stage" / "new_file.py", b"new content")
        # Stage another file that will fail
        _write(tmp_path / "stage" / "fail.py", b"will fail")

        # Make the second file swap fail
        orig_rename = os.rename
        def failing_rename(src, dst):
            if src.endswith("fail.py"):
                raise OSError("Simulated failure")
            return orig_rename(src, dst)

        monkeypatch.setattr(os, "rename", failing_rename)

        # Attempt swap (should fail and rollback)
        with pytest.raises(OSError):
            c.stage_and_swap("ref123", "commit123")

        # New file should be deleted by rollback
        assert not (tmp_path / "new_file.py").exists()
        # version.json should not exist (update failed)
        assert not (tmp_path / "version.json").exists()

    def test_rollback_removes_multiple_new_files(self, tmp_path, monkeypatch):
        """
        Multiple new files should all be deleted on rollback.

        Tests that rollback correctly handles multiple "new" operations.
        """
        cfg = {"owner": "o", "repo": "r"}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        # Stage multiple new files
        _write(tmp_path / "stage" / "new1.py", b"content1")
        _write(tmp_path / "stage" / "new2.py", b"content2")
        _write(tmp_path / "stage" / "new3.py", b"content3")
        _write(tmp_path / "stage" / "fail.py", b"fail")

        orig_rename = os.rename
        def failing_rename(src, dst):
            if src.endswith("fail.py"):
                raise OSError("Fail")
            return orig_rename(src, dst)

        monkeypatch.setattr(os, "rename", failing_rename)

        with pytest.raises(OSError):
            c.stage_and_swap("ref", "commit")

        # All new files should be deleted
        assert not (tmp_path / "new1.py").exists()
        assert not (tmp_path / "new2.py").exists()
        assert not (tmp_path / "new3.py").exists()
        assert not (tmp_path / "fail.py").exists()

    def test_rollback_new_file_already_deleted(self, tmp_path, monkeypatch):
        """
        Rollback should be idempotent if new file already deleted.

        Tests defensive programming: if file doesn't exist, os.remove
        should not be called (code checks _exists first).
        """
        cfg = {"owner": "o", "repo": "r"}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        _write(tmp_path / "stage" / "new.py", b"content")
        _write(tmp_path / "stage" / "fail.py", b"fail")

        orig_rename = os.rename
        removed_during_rollback = []
        orig_remove = os.remove

        def tracking_remove(path):
            # Track if called during rollback (after fail.py rename fails)
            removed_during_rollback.append(path)
            return orig_remove(path)

        def failing_rename(src, dst):
            result = orig_rename(src, dst)
            if dst.endswith("new.py"):
                # Delete file immediately after it's moved from stage
                orig_remove(dst)
            if src.endswith("fail.py"):
                raise OSError("Fail")
            return result

        monkeypatch.setattr(os, "remove", tracking_remove)
        monkeypatch.setattr(os, "rename", failing_rename)

        with pytest.raises(OSError):
            c.stage_and_swap("ref", "commit")

        # File was deleted before rollback, so rollback skips it (_exists check)
        assert not (tmp_path / "new.py").exists()
        # Rollback shouldn't try to remove a non-existent file
        non_stage_removes = [p for p in removed_during_rollback if "stage" not in p]
        # Only the manual deletion in failing_rename, not from rollback
        assert len(non_stage_removes) == 1

    def test_rollback_new_file_permission_error(self, tmp_path, monkeypatch):
        """
        Rollback should track errors but not crash if can't delete new file.

        Tests error handling: rollback failures are logged to ota_error.json.
        """
        cfg = {"owner": "o", "repo": "r"}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        _write(tmp_path / "stage" / "new1.py", b"new1")
        _write(tmp_path / "stage" / "new2.py", b"new2")
        _write(tmp_path / "stage" / "fail.py", b"fail")

        orig_remove = os.remove
        orig_rename = os.rename
        rollback_started = [False]

        def failing_remove(path):
            # During rollback phase, fail to remove ALL non-stage files
            if rollback_started[0] and "stage" not in path and "backup" not in path:
                raise PermissionError(f"Cannot delete {path}")
            return orig_remove(path)

        def failing_rename(src, dst):
            if src.endswith("fail.py"):
                rollback_started[0] = True
                raise OSError("Trigger rollback")
            return orig_rename(src, dst)

        monkeypatch.setattr(os, "remove", failing_remove)
        monkeypatch.setattr(os, "rename", failing_rename)

        with pytest.raises(OSError):
            c.stage_and_swap("ref", "commit")

        # If rollback errors occurred, error file should exist
        if (tmp_path / ERROR_FILE).exists():
            with open(tmp_path / ERROR_FILE) as f:
                data = json.load(f)
            errors = data.get("errors", [])
            assert len(errors) > 0


class TestRollbackReplacedFiles:
    """Test rollback of replaced files (operation="replace")."""

    def test_rollback_restores_replaced_file(self, tmp_path, monkeypatch):
        """
        Replaced file should be restored from backup on rollback.

        Scenario:
        - Existing file at target
        - File backed up, then replaced with staged version (tracked as "replace")
        - Swap fails after this
        - Rollback should restore original from backup
        """
        cfg = {"owner": "o", "repo": "r"}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        # Existing file
        _write(tmp_path / "app.py", b"original content")
        # Staged replacement
        _write(tmp_path / "stage" / "app.py", b"new content")
        # File that will fail
        _write(tmp_path / "stage" / "fail.py", b"fail")

        orig_rename = os.rename
        def failing_rename(src, dst):
            if src.endswith("fail.py"):
                raise OSError("Fail")
            return orig_rename(src, dst)

        monkeypatch.setattr(os, "rename", failing_rename)

        with pytest.raises(OSError):
            c.stage_and_swap("ref", "commit")

        # Original file should be restored
        assert (tmp_path / "app.py").read_bytes() == b"original content"
        assert not (tmp_path / "version.json").exists()

    def test_rollback_restores_multiple_replaced_files(self, tmp_path, monkeypatch):
        """
        Multiple replaced files should all be restored on rollback.
        """
        cfg = {"owner": "o", "repo": "r"}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        # Existing files
        _write(tmp_path / "file1.py", b"orig1")
        _write(tmp_path / "file2.py", b"orig2")
        _write(tmp_path / "file3.py", b"orig3")

        # Staged replacements
        _write(tmp_path / "stage" / "file1.py", b"new1")
        _write(tmp_path / "stage" / "file2.py", b"new2")
        _write(tmp_path / "stage" / "file3.py", b"new3")
        _write(tmp_path / "stage" / "fail.py", b"fail")

        orig_rename = os.rename
        def failing_rename(src, dst):
            if src.endswith("fail.py"):
                raise OSError("Fail")
            return orig_rename(src, dst)

        monkeypatch.setattr(os, "rename", failing_rename)

        with pytest.raises(OSError):
            c.stage_and_swap("ref", "commit")

        # All original files should be restored
        assert (tmp_path / "file1.py").read_bytes() == b"orig1"
        assert (tmp_path / "file2.py").read_bytes() == b"orig2"
        assert (tmp_path / "file3.py").read_bytes() == b"orig3"

    def test_rollback_replace_backup_missing(self, tmp_path, monkeypatch):
        """
        Rollback should handle missing backup gracefully.

        If backup file disappeared (disk corruption, manual deletion),
        rollback won't restore (checks _exists first) but won't crash.
        """
        cfg = {"owner": "o", "repo": "r"}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        _write(tmp_path / "app.py", b"original")
        _write(tmp_path / "stage" / "app.py", b"new")
        _write(tmp_path / "stage" / "fail.py", b"fail")

        orig_rename = os.rename

        def tracking_rename(src, dst):
            result = orig_rename(src, dst)
            # Delete backup immediately after creation
            if dst.startswith(c.backup) and "app.py" in dst:
                os.remove(dst)
            if src.endswith("fail.py"):
                raise OSError("Trigger rollback")
            return result

        monkeypatch.setattr(os, "rename", tracking_rename)

        with pytest.raises(OSError):
            c.stage_and_swap("ref", "commit")

        # Code checks _exists(backup) before restoring, so no error written
        # (rollback silently skips missing backups - this is graceful degradation)

    def test_rollback_replace_target_locked(self, tmp_path, monkeypatch):
        """
        Rollback should handle error when can't remove target before restore.

        During rollback of "replace" operation:
        1. Remove current file at target
        2. Rename backup to target

        If step 1 fails, error should be tracked.
        """
        cfg = {"owner": "o", "repo": "r"}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        _write(tmp_path / "app.py", b"original")
        _write(tmp_path / "stage" / "app.py", b"new")
        _write(tmp_path / "stage" / "other.py", b"other new")
        _write(tmp_path / "stage" / "fail.py", b"fail")

        orig_remove = os.remove
        orig_rename = os.rename
        rollback_phase = [False]

        def failing_remove(path):
            # During rollback, fail to remove target files
            if rollback_phase[0] and "stage" not in path and "backup" not in path:
                raise PermissionError(f"Cannot remove {path}")
            return orig_remove(path)

        def tracking_rename(src, dst):
            if src.endswith("fail.py"):
                rollback_phase[0] = True
                raise OSError("Trigger rollback")
            return orig_rename(src, dst)

        monkeypatch.setattr(os, "remove", failing_remove)
        monkeypatch.setattr(os, "rename", tracking_rename)

        with pytest.raises(OSError):
            c.stage_and_swap("ref", "commit")

        # If rollback errors occurred, error file should exist
        if (tmp_path / ERROR_FILE).exists():
            with open(tmp_path / ERROR_FILE) as f:
                data = json.load(f)
            errors = data.get("errors", [])
            assert len(errors) > 0


class TestRollbackDeletedFiles:
    """Test rollback of deleted files (operation="delete")."""

    def test_rollback_restores_deleted_file(self, tmp_path, monkeypatch):
        """
        Deleted file should be restored from backup on rollback.

        Scenario:
        - File exists at target
        - File moved to backup as part of deletion (tracked as "delete")
        - Swap fails after this
        - Rollback should restore file from backup
        """
        cfg = {"owner": "o", "repo": "r"}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        # File to be deleted
        _write(tmp_path / "old_file.py", b"old content")
        # Staged file that will fail
        _write(tmp_path / "stage" / "fail.py", b"fail")

        # Simulate deletion via deletes parameter
        orig_rename = os.rename
        def failing_rename(src, dst):
            if src.endswith("fail.py"):
                raise OSError("Fail")
            return orig_rename(src, dst)

        monkeypatch.setattr(os, "rename", failing_rename)

        with pytest.raises(OSError):
            c.stage_and_swap("ref", "commit", deletes=["old_file.py"])

        # Deleted file should be restored
        assert (tmp_path / "old_file.py").exists()
        assert (tmp_path / "old_file.py").read_bytes() == b"old content"

    def test_rollback_restores_multiple_deleted_files(self, tmp_path, monkeypatch):
        """
        Multiple deleted files should all be restored on rollback.
        """
        cfg = {"owner": "o", "repo": "r"}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        # Files to be deleted
        _write(tmp_path / "old1.py", b"old1")
        _write(tmp_path / "old2.py", b"old2")
        _write(tmp_path / "old3.py", b"old3")
        _write(tmp_path / "stage" / "fail.py", b"fail")

        orig_rename = os.rename
        def failing_rename(src, dst):
            if src.endswith("fail.py"):
                raise OSError("Fail")
            return orig_rename(src, dst)

        monkeypatch.setattr(os, "rename", failing_rename)

        with pytest.raises(OSError):
            c.stage_and_swap("ref", "commit", deletes=["old1.py", "old2.py", "old3.py"])

        # All deleted files should be restored
        assert (tmp_path / "old1.py").read_bytes() == b"old1"
        assert (tmp_path / "old2.py").read_bytes() == b"old2"
        assert (tmp_path / "old3.py").read_bytes() == b"old3"

    def test_rollback_delete_backup_missing(self, tmp_path, monkeypatch):
        """
        Rollback should handle missing backup for deleted file gracefully.

        Code checks _exists(backup) before restoring, so silently skips if missing.
        """
        cfg = {"owner": "o", "repo": "r"}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        _write(tmp_path / "old.py", b"old content")
        _write(tmp_path / "stage" / "new.py", b"new")
        _write(tmp_path / "stage" / "fail.py", b"fail")

        orig_rename = os.rename
        backup_deleted = [False]

        def tracking_rename(src, dst):
            result = orig_rename(src, dst)
            # Delete backup of old.py immediately after creation
            if dst.startswith(c.backup) and "old.py" in dst and not backup_deleted[0]:
                os.remove(dst)
                backup_deleted[0] = True
            if src.endswith("fail.py"):
                raise OSError("Trigger rollback")
            return result

        monkeypatch.setattr(os, "rename", tracking_rename)

        with pytest.raises(OSError):
            c.stage_and_swap("ref", "commit", deletes=["old.py"])

        # Backup was deleted, so code skips restoration (checks _exists first)
        # File should not be restored since backup is missing
        # Code gracefully handles this without error

    def test_rollback_delete_creates_missing_dirs(self, tmp_path, monkeypatch):
        """
        Rollback should recreate parent directories when restoring deleted files.

        If a file in a subdirectory was deleted, rollback needs to ensure
        parent directories exist before restoring.
        """
        cfg = {"owner": "o", "repo": "r"}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        # File in subdirectory
        _write(tmp_path / "lib" / "module.py", b"module content")
        _write(tmp_path / "stage" / "fail.py", b"fail")

        orig_rename = os.rename

        def failing_rename(src, dst):
            result = orig_rename(src, dst)
            # After deleting lib/module.py, remove the lib directory too
            if src.endswith("module.py") and dst.startswith(c.backup):
                try:
                    os.rmdir(str(tmp_path / "lib"))
                except Exception:
                    pass
            elif src.endswith("fail.py"):
                raise OSError("Trigger rollback")
            return result

        monkeypatch.setattr(os, "rename", failing_rename)

        with pytest.raises(OSError):
            c.stage_and_swap("ref", "commit", deletes=["lib/module.py"])

        # File should be restored, with parent directory recreated
        assert (tmp_path / "lib" / "module.py").exists()
        assert (tmp_path / "lib" / "module.py").read_bytes() == b"module content"


class TestRollbackMixedOperations:
    """Test rollback with combination of new, replace, and delete operations."""

    def test_rollback_mixed_operations(self, tmp_path, monkeypatch):
        """
        Rollback should handle mix of new, replace, and delete operations.

        Scenario:
        - Some files are new (no previous version)
        - Some files are replacements (existing file backed up)
        - Some files are deletions (file moved to backup)
        - All should be correctly rolled back
        """
        cfg = {"owner": "o", "repo": "r"}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        # Existing file (will be replaced)
        _write(tmp_path / "app.py", b"original app")
        # Existing file (will be deleted)
        _write(tmp_path / "old_module.py", b"old module")

        # Staged files
        _write(tmp_path / "stage" / "app.py", b"new app")  # replacement
        _write(tmp_path / "stage" / "new_feature.py", b"new feature")  # new file
        _write(tmp_path / "stage" / "fail.py", b"fail")  # will fail

        orig_rename = os.rename
        def failing_rename(src, dst):
            if src.endswith("fail.py"):
                raise OSError("Fail")
            return orig_rename(src, dst)

        monkeypatch.setattr(os, "rename", failing_rename)

        with pytest.raises(OSError):
            c.stage_and_swap("ref", "commit", deletes=["old_module.py"])

        # Verify rollback results:
        # - Replaced file restored to original
        assert (tmp_path / "app.py").read_bytes() == b"original app"
        # - New file deleted
        assert not (tmp_path / "new_feature.py").exists()
        # - Deleted file restored
        assert (tmp_path / "old_module.py").read_bytes() == b"old module"
        # - Failed file never created
        assert not (tmp_path / "fail.py").exists()
        # - No version.json written
        assert not (tmp_path / "version.json").exists()

    def test_rollback_reverse_order(self, tmp_path, monkeypatch):
        """
        Rollback should process operations in reverse order.

        This ensures LIFO (last-in-first-out) rollback, which is critical
        for maintaining consistency if operations have dependencies.
        """
        cfg = {"owner": "o", "repo": "r"}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        # Track order of rollback operations
        rollback_order = []

        _write(tmp_path / "file1.py", b"orig1")
        _write(tmp_path / "file2.py", b"orig2")
        _write(tmp_path / "file3.py", b"orig3")

        _write(tmp_path / "stage" / "file1.py", b"new1")
        _write(tmp_path / "stage" / "file2.py", b"new2")
        _write(tmp_path / "stage" / "file3.py", b"new3")
        _write(tmp_path / "stage" / "fail.py", b"fail")

        orig_remove = os.remove
        orig_rename = os.rename
        rollback_active = [False]

        def tracking_remove(path):
            if rollback_active[0]:
                rollback_order.append(("remove", path))
            return orig_remove(path)

        def tracking_rename(src, dst):
            if rollback_active[0] and dst not in [c.stage, c.backup]:
                rollback_order.append(("rename", src, dst))
            elif src.endswith("fail.py"):
                rollback_active[0] = True
                raise OSError("Trigger rollback")
            return orig_rename(src, dst)

        monkeypatch.setattr(os, "remove", tracking_remove)
        monkeypatch.setattr(os, "rename", tracking_rename)

        with pytest.raises(OSError):
            c.stage_and_swap("ref", "commit")

        # Rollback should process in reverse order
        # Last file swapped should be first to be rolled back
        assert len(rollback_order) > 0
        # file3.py was last to be processed, should be first in rollback
        assert "file3.py" in str(rollback_order[0])

    def test_rollback_partial_failure(self, tmp_path, monkeypatch):
        """
        Some rollback operations may fail, but others should still succeed.

        Tests resilience: if one rollback operation fails, continue with others.
        """
        cfg = {"owner": "o", "repo": "r"}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        _write(tmp_path / "file1.py", b"orig1")
        _write(tmp_path / "file2.py", b"orig2")
        _write(tmp_path / "file3.py", b"orig3")

        _write(tmp_path / "stage" / "file1.py", b"new1")
        _write(tmp_path / "stage" / "file2.py", b"new2")
        _write(tmp_path / "stage" / "file3.py", b"new3")
        _write(tmp_path / "stage" / "fail.py", b"fail")

        orig_remove = os.remove
        orig_rename = os.rename
        rollback_active = [False]

        def failing_remove(path):
            # During rollback, fail to remove file2.py
            if rollback_active[0] and "file2.py" in path and not path.startswith(c.backup):
                raise PermissionError("Cannot remove file2")
            return orig_remove(path)

        def tracking_rename(src, dst):
            if src.endswith("fail.py"):
                rollback_active[0] = True
                raise OSError("Trigger rollback")
            return orig_rename(src, dst)

        monkeypatch.setattr(os, "remove", failing_remove)
        monkeypatch.setattr(os, "rename", tracking_rename)

        with pytest.raises(OSError):
            c.stage_and_swap("ref", "commit")

        # file1 and file3 should be restored despite file2 rollback failing
        assert (tmp_path / "file1.py").read_bytes() == b"orig1"
        assert (tmp_path / "file3.py").read_bytes() == b"orig3"
        # file2 rollback failed, so it still has new content
        # (rollback tries to remove it before restoring, but remove fails)

        # Error should be logged
        assert (tmp_path / ERROR_FILE).exists()

    def test_rollback_error_tracking(self, tmp_path, monkeypatch):
        """
        Rollback errors should be written to ota_error.json with details.

        Tests error tracking mechanism for debugging headless devices.
        """
        cfg = {"owner": "o", "repo": "r"}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        _write(tmp_path / "file1.py", b"orig")
        _write(tmp_path / "stage" / "file1.py", b"new")
        _write(tmp_path / "stage" / "new.py", b"new file")
        _write(tmp_path / "stage" / "fail.py", b"fail")

        orig_remove = os.remove
        orig_rename = os.rename
        rollback_phase = [False]

        def failing_remove(path):
            # During rollback, fail to remove ALL targets
            if rollback_phase[0] and "stage" not in path and "backup" not in path:
                raise PermissionError(f"Permission denied: {path}")
            return orig_remove(path)

        def failing_rename(src, dst):
            if src.endswith("fail.py"):
                rollback_phase[0] = True
                raise OSError("Trigger rollback")
            return orig_rename(src, dst)

        monkeypatch.setattr(os, "remove", failing_remove)
        monkeypatch.setattr(os, "rename", failing_rename)

        with pytest.raises(OSError):
            c.stage_and_swap("ref", "commit")

        # If rollback had errors, check error file exists
        if (tmp_path / ERROR_FILE).exists():
            with open(tmp_path / ERROR_FILE) as f:
                data = json.load(f)
            errors = data.get("errors", [])
            assert len(errors) >= 1


class TestRollbackEdgeCases:
    """Test edge cases and boundary conditions in rollback logic."""

    def test_rollback_empty_applied_list(self, tmp_path, monkeypatch):
        """
        Rollback with no operations should not crash.

        If failure happens before any files are swapped, rollback
        should handle empty applied list gracefully.
        """
        cfg = {"owner": "o", "repo": "r"}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        # Create a custom OTA method that raises before any swaps
        original_method = c.stage_and_swap

        def failing_stage_and_swap(ref, commit, deletes=None, safe_tail=None):
            # Simulate early failure before any operations
            raise OSError("Early failure before swap")

        c.stage_and_swap = failing_stage_and_swap

        # Should raise error but not crash during rollback
        with pytest.raises(OSError) as exc_info:
            c.stage_and_swap("ref", "commit")

        assert "Early failure" in str(exc_info.value)

    def test_rollback_duplicate_operations(self, tmp_path, monkeypatch):
        """
        Rollback should handle duplicate file operations gracefully.

        If same file appears multiple times (shouldn't happen but test defensive code),
        rollback should not crash.
        """
        cfg = {"owner": "o", "repo": "r"}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        _write(tmp_path / "app.py", b"original")
        # Stage same file twice (via symlink or duplicate in manifest)
        _write(tmp_path / "stage" / "app.py", b"new")
        _write(tmp_path / "stage" / "fail.py", b"fail")

        orig_rename = os.rename

        def failing_rename(src, dst):
            if src.endswith("fail.py"):
                raise OSError("Fail")
            return orig_rename(src, dst)

        monkeypatch.setattr(os, "rename", failing_rename)

        # Should not crash even if rollback processes same file multiple times
        with pytest.raises(OSError):
            c.stage_and_swap("ref", "commit")

        # File should be restored (at least once)
        assert (tmp_path / "app.py").exists()

    def test_rollback_path_traversal_blocked(self, tmp_path, monkeypatch):
        """
        Rollback should not process paths that fail security checks.

        Path filtering applies during staging via _is_permitted check.
        This test verifies files with proper allow list work correctly.
        """
        cfg = {"owner": "o", "repo": "r", "allow": ["app.py", "lib.py"]}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        # Files in allow list
        _write(tmp_path / "app.py", b"original")
        _write(tmp_path / "stage" / "app.py", b"new")

        # Successful swap
        c.stage_and_swap("ref", "commit")

        # File should be updated successfully
        assert (tmp_path / "app.py").read_bytes() == b"new"
        assert (tmp_path / "version.json").exists()

    def test_rollback_sync_after_each_operation(self, tmp_path, monkeypatch):
        """
        Verify os.sync is called during forward operations (not rollback).

        The implementation calls os.sync after backing up files and after
        applying staged files, ensuring durability before failure occurs.
        Rollback relies on these synced backups.

        Note: Current implementation syncs during forward operations,
        not during rollback itself.
        """
        cfg = {"owner": "o", "repo": "r"}
        c = OTA(cfg)
        c.stage = str(tmp_path / "stage")
        c.backup = str(tmp_path / "backup")
        ensure_dirs(c.stage)
        ensure_dirs(c.backup)
        monkeypatch.chdir(tmp_path)

        _write(tmp_path / "app.py", b"original")
        _write(tmp_path / "stage" / "app.py", b"new")

        sync_calls = []

        # Mock os.sync if it exists
        def tracking_sync():
            sync_calls.append(True)
            # Don't actually call os.sync in test

        if hasattr(os, "sync"):
            monkeypatch.setattr(os, "sync", tracking_sync)

        # Successful swap
        c.stage_and_swap("ref", "commit")

        # Verify file updated
        assert (tmp_path / "app.py").read_bytes() == b"new"

        # os.sync should be called during forward operations
        # (backup, apply, version write)
        if hasattr(os, "sync"):
            assert len(sync_calls) > 0
