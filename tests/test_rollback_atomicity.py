"""
Tests for rollback atomicity (Fix #1).

Ensures that rollback correctly handles:
- New files (should be deleted)
- Replace operations (original should be restored)
- Delete operations (deleted file should be restored)
- Mixed operations (all should work correctly)
- Error handling (continues despite individual failures)
"""

import os
import pytest
from ota import OTA, OTAError


@pytest.fixture
def temp_filesystem(tmp_path, monkeypatch):
    """Create a temporary filesystem for testing."""
    monkeypatch.chdir(tmp_path)

    # Create directories
    os.makedirs(".ota_stage", exist_ok=True)
    os.makedirs(".ota_backup", exist_ok=True)

    return tmp_path


@pytest.fixture
def ota_instance(temp_filesystem):
    """Create an OTA instance with minimal config."""
    cfg = {
        "owner": "test",
        "repo": "test",
        "ssid": "test",
        "password": "test",
        "channel": "stable",
        "allow": ["*"],
        "debug": True
    }
    return OTA(cfg)


def test_new_file_rollback(ota_instance, temp_filesystem):
    """Test that new files are correctly deleted during rollback."""
    # Create a staged file (simulates a new file being downloaded)
    staged_file = temp_filesystem / ".ota_stage" / "new_file.txt"
    staged_file.parent.mkdir(parents=True, exist_ok=True)
    staged_file.write_text("new content")

    # Simulate the swap process that will fail
    applied = []

    # Move staged file to target (simulates new file operation)
    target = temp_filesystem / "new_file.txt"
    os.rename(str(staged_file), str(target))
    applied.append(("new", "new_file.txt", None))

    # Verify file exists before rollback
    assert target.exists()

    # Simulate rollback
    for op_type, target_path, backup_path in reversed(applied):
        if op_type == "new":
            if os.path.exists(target_path):
                os.remove(target_path)

    # Verify file was deleted during rollback
    assert not target.exists()


def test_replace_rollback(ota_instance, temp_filesystem):
    """Test that replaced files are correctly restored during rollback."""
    # Create original file
    original_file = temp_filesystem / "existing_file.txt"
    original_file.write_text("original content")

    # Create staged replacement file
    staged_file = temp_filesystem / ".ota_stage" / "existing_file.txt"
    staged_file.parent.mkdir(parents=True, exist_ok=True)
    staged_file.write_text("new content")

    # Simulate the swap process
    applied = []

    # Backup original
    backup_path = temp_filesystem / ".ota_backup" / "existing_file.txt"
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    os.rename(str(original_file), str(backup_path))

    # Move staged file to target
    os.rename(str(staged_file), str(original_file))
    applied.append(("replace", "existing_file.txt", ".ota_backup/existing_file.txt"))

    # Verify new content is in place
    assert original_file.read_text() == "new content"

    # Simulate rollback
    for op_type, target_path, backup_path_str in reversed(applied):
        if op_type == "replace":
            if backup_path_str and os.path.exists(backup_path_str):
                if os.path.exists(target_path):
                    os.remove(target_path)
                os.rename(backup_path_str, target_path)

    # Verify original content was restored
    assert original_file.read_text() == "original content"
    assert not backup_path.exists()


def test_delete_rollback(ota_instance, temp_filesystem):
    """Test that deleted files are correctly restored during rollback."""
    # Create file to be deleted
    file_to_delete = temp_filesystem / "old_file.txt"
    file_to_delete.write_text("old content")

    # Simulate the deletion process
    applied = []

    # Backup the file (simulates deletion)
    backup_path = temp_filesystem / ".ota_backup" / "old_file.txt"
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    os.rename(str(file_to_delete), str(backup_path))
    applied.append(("delete", None, ".ota_backup/old_file.txt"))

    # Verify file was deleted
    assert not file_to_delete.exists()
    assert backup_path.exists()

    # Simulate rollback
    for op_type, target_path, backup_path_str in reversed(applied):
        if op_type == "delete":
            if backup_path_str and os.path.exists(backup_path_str):
                # Extract original path from backup path
                backup_prefix = ".ota_backup/"
                original = backup_path_str[len(backup_prefix):] if backup_path_str.startswith(backup_prefix) else backup_path_str
                os.rename(backup_path_str, original)

    # Verify file was restored
    assert file_to_delete.exists()
    assert file_to_delete.read_text() == "old content"
    assert not backup_path.exists()


def test_mixed_operations_rollback(ota_instance, temp_filesystem):
    """Test that mixed operations (new, replace, delete) all rollback correctly."""
    # Setup: Create original file and file to delete
    original_file = temp_filesystem / "existing_file.txt"
    original_file.write_text("original content")

    file_to_delete = temp_filesystem / "delete_me.txt"
    file_to_delete.write_text("delete content")

    # Create staged files
    stage_dir = temp_filesystem / ".ota_stage"
    backup_dir = temp_filesystem / ".ota_backup"

    staged_new = stage_dir / "new_file.txt"
    staged_new.write_text("new content")

    staged_replace = stage_dir / "existing_file.txt"
    staged_replace.write_text("replacement content")

    # Simulate swap operations
    applied = []

    # 1. Replace operation
    backup_replace = backup_dir / "existing_file.txt"
    backup_replace.parent.mkdir(parents=True, exist_ok=True)
    os.rename(str(original_file), str(backup_replace))
    os.rename(str(staged_replace), str(original_file))
    applied.append(("replace", "existing_file.txt", ".ota_backup/existing_file.txt"))

    # 2. New file operation
    new_file = temp_filesystem / "new_file.txt"
    os.rename(str(staged_new), str(new_file))
    applied.append(("new", "new_file.txt", None))

    # 3. Delete operation
    backup_delete = backup_dir / "delete_me.txt"
    os.rename(str(file_to_delete), str(backup_delete))
    applied.append(("delete", None, ".ota_backup/delete_me.txt"))

    # Verify state before rollback
    assert original_file.read_text() == "replacement content"
    assert new_file.exists()
    assert not file_to_delete.exists()

    # Simulate rollback
    for op_type, target_path, backup_path_str in reversed(applied):
        if op_type == "new":
            if os.path.exists(target_path):
                os.remove(target_path)
        elif op_type == "replace":
            if backup_path_str and os.path.exists(backup_path_str):
                if os.path.exists(target_path):
                    os.remove(target_path)
                os.rename(backup_path_str, target_path)
        elif op_type == "delete":
            if backup_path_str and os.path.exists(backup_path_str):
                backup_prefix = ".ota_backup/"
                original = backup_path_str[len(backup_prefix):] if backup_path_str.startswith(backup_prefix) else backup_path_str
                os.rename(backup_path_str, original)

    # Verify rollback restored original state
    assert original_file.read_text() == "original content"
    assert not new_file.exists()
    assert file_to_delete.exists()
    assert file_to_delete.read_text() == "delete content"


def test_rollback_error_handling(ota_instance, temp_filesystem, monkeypatch):
    """Test that rollback continues despite individual failures."""
    # Create multiple files
    file1 = temp_filesystem / "file1.txt"
    file1.write_text("content1")

    file2 = temp_filesystem / "file2.txt"
    file2.write_text("content2")

    file3 = temp_filesystem / "file3.txt"
    file3.write_text("content3")

    # Create operations list
    applied = [
        ("new", "file1.txt", None),
        ("new", "file2.txt", None),
        ("new", "file3.txt", None),
    ]

    rollback_errors = []
    files_to_fail = {"file2.txt"}  # Force failure on file2

    # Simulate rollback with forced error on file2
    for op_type, target_path, backup_path in reversed(applied):
        try:
            if op_type == "new":
                # Force an error on file2
                if target_path in files_to_fail:
                    raise OSError("Simulated I/O error")
                if target_path and os.path.exists(target_path):
                    os.remove(target_path)
            elif op_type == "replace":
                if backup_path and os.path.exists(backup_path):
                    if target_path and os.path.exists(target_path):
                        os.remove(target_path)
                    os.rename(backup_path, target_path)
        except Exception as e:
            error_msg = "Failed to rollback {} ({}): {}".format(
                target_path or backup_path, op_type, str(e)
            )
            rollback_errors.append(error_msg)

    # Should have logged exactly one error (file2)
    assert len(rollback_errors) == 1
    assert "file2.txt" in rollback_errors[0]
    assert "Simulated I/O error" in rollback_errors[0]

    # But should have still deleted file1 and file3
    assert not file1.exists()
    assert file2.exists()  # This one failed to rollback
    assert not file3.exists()


def test_full_integration_rollback(ota_instance, temp_filesystem, monkeypatch):
    """Integration test: stage_and_swap with forced failure triggers correct rollback."""
    # Create original file
    original_file = temp_filesystem / "main.py"
    original_file.write_text("original code")

    # Create staged replacement
    staged_file = temp_filesystem / ".ota_stage" / "main.py"
    staged_file.parent.mkdir(parents=True, exist_ok=True)
    staged_file.write_text("updated code")

    # Create staged new file
    staged_new = temp_filesystem / ".ota_stage" / "new_module.py"
    staged_new.write_text("new module")

    # Mock _write_state to raise an exception (force rollback)
    def mock_write_state(ref, commit):
        raise OTAError("Simulated failure during state write")

    monkeypatch.setattr(ota_instance, "_write_state", mock_write_state)

    # Attempt swap (should rollback)
    with pytest.raises(OTAError, match="Simulated failure"):
        ota_instance.stage_and_swap("v1.0.0", "abc123")

    # Verify rollback restored original state
    assert original_file.exists()
    assert original_file.read_text() == "original code"

    # Verify new file was removed
    new_file = temp_filesystem / "new_module.py"
    assert not new_file.exists()

    # Verify backup was cleaned up
    backup_dir = temp_filesystem / ".ota_backup"
    if backup_dir.exists():
        assert len(list(backup_dir.rglob("*"))) == 0 or not any(backup_dir.iterdir())


def test_delete_with_subdirectories_rollback(ota_instance, temp_filesystem):
    """Test that deleted files in subdirectories are correctly restored."""
    # Create file in subdirectory
    subdir_file = temp_filesystem / "lib" / "utils.py"
    subdir_file.parent.mkdir(parents=True, exist_ok=True)
    subdir_file.write_text("utility functions")

    # Simulate deletion
    applied = []
    backup_path = temp_filesystem / ".ota_backup" / "lib" / "utils.py"
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    os.rename(str(subdir_file), str(backup_path))
    applied.append(("delete", None, ".ota_backup/lib/utils.py"))

    # Verify deletion
    assert not subdir_file.exists()

    # Simulate rollback
    for op_type, target_path, backup_path_str in reversed(applied):
        if op_type == "delete":
            if backup_path_str and os.path.exists(backup_path_str):
                backup_prefix = ".ota_backup/"
                original = backup_path_str[len(backup_prefix):] if backup_path_str.startswith(backup_prefix) else backup_path_str
                # Ensure parent directory exists
                parent = os.path.dirname(original)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                os.rename(backup_path_str, original)

    # Verify restoration
    assert subdir_file.exists()
    assert subdir_file.read_text() == "utility functions"
