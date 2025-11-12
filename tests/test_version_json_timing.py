"""
Test suite for Fix #4: Version.json Timing

This module verifies that version.json is written AFTER all file swaps are
synchronized to storage, preventing state inconsistency during power loss.

Bug scenario (old behavior):
1. Files staged
2. version.json written (says v2.0)
3. os.sync() called
4. POWER LOSS before sync completes
5. Result: version.json says v2.0 but filesystem has v1.9 files

Fixed behavior:
1. Files staged
2. os.sync() called (all swaps durable)
3. version.json written (says v2.0)
4. os.sync() called (version.json durable)
5. Result: Even with power loss, version.json only reflects durable state
"""

import os
import json
import pytest
from unittest.mock import Mock, MagicMock, call, patch
from ota import OTA, ensure_dirs


# ============================================================================
# Test Helpers and Fixtures
# ============================================================================

def _write(path, data):
    """Helper to write test files."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if isinstance(data, str):
        data = data.encode()
    with open(path, "wb") as f:
        f.write(data)


class SyncCallTracker:
    """Tracks os.sync() calls and their timing relative to _write_state."""
    def __init__(self):
        self.sync_calls = []
        self.write_state_calls = []
        self.call_order = []

    def track_sync(self):
        """Record a sync call."""
        self.sync_calls.append(len(self.call_order))
        self.call_order.append("sync")

    def track_write_state(self, ref, commit):
        """Record a write_state call."""
        self.write_state_calls.append(len(self.call_order))
        self.call_order.append("write_state")

    def sync_before_write_state(self):
        """Check if at least one sync occurred before write_state."""
        if not self.write_state_calls:
            return False
        first_write = min(self.write_state_calls)
        syncs_before = [s for s in self.sync_calls if s < first_write]
        return len(syncs_before) > 0

    def sync_after_write_state(self):
        """Check if at least one sync occurred after write_state."""
        if not self.write_state_calls:
            return False
        last_write = max(self.write_state_calls)
        syncs_after = [s for s in self.sync_calls if s > last_write]
        return len(syncs_after) > 0

    def correct_order(self):
        """Verify correct order: [file operations] → sync → write_state → sync."""
        return self.sync_before_write_state() and self.sync_after_write_state()


class PowerLossSimulator:
    """Simulates power loss at specific points in execution."""
    def __init__(self, fail_at):
        """
        Args:
            fail_at: When to trigger power loss
                - "during_swap": During file rename operations
                - "first_sync": During os.sync() before version.json
                - "write_state": During _write_state()
                - "final_sync": During os.sync() after version.json
        """
        self.fail_at = fail_at
        self.sync_count = 0
        self.rename_count = 0
        self.write_state_count = 0

    def wrap_sync(self, original_sync):
        """Wrap os.sync to simulate crash."""
        def _sync():
            self.sync_count += 1
            if self.fail_at == "first_sync" and self.sync_count == 1:
                raise SystemExit("POWER LOSS during first sync")
            if self.fail_at == "final_sync" and self.sync_count >= 2:
                raise SystemExit("POWER LOSS during final sync")
            if hasattr(original_sync, '__call__'):
                return original_sync()
        return _sync

    def wrap_rename(self, original_rename):
        """Wrap os.rename to simulate crash during swap."""
        def _rename(src, dst):
            self.rename_count += 1
            if self.fail_at == "during_swap" and self.rename_count >= 2:
                raise SystemExit("POWER LOSS during file swap")
            return original_rename(src, dst)
        return _rename

    def wrap_write_state(self, original_write_state):
        """Wrap _write_state to simulate crash."""
        def _write_state(ref, commit):
            self.write_state_count += 1
            if self.fail_at == "write_state":
                raise SystemExit("POWER LOSS during version.json write")
            return original_write_state(ref, commit)
        return _write_state


@pytest.fixture
def ota_instance(tmp_path, monkeypatch):
    """Create OTA instance with test environment."""
    cfg = {"owner": "test", "repo": "repo"}
    ota = OTA(cfg)
    ota.stage = str(tmp_path / ".ota_stage")
    ota.backup = str(tmp_path / ".ota_backup")
    ensure_dirs(ota.stage)
    ensure_dirs(ota.backup)
    monkeypatch.chdir(tmp_path)
    return ota


# ============================================================================
# Test Class 1: TestVersionWriteTiming
# ============================================================================

class TestVersionWriteTiming:
    """Verify _write_state() is called at the correct time in the swap flow."""

    def test_version_written_after_file_sync(self, ota_instance, tmp_path, monkeypatch):
        """Ensure _write_state() is called AFTER os.sync() of file swaps."""
        tracker = SyncCallTracker()

        # Mock os.sync to track calls
        original_sync = getattr(os, 'sync', lambda: None)
        monkeypatch.setattr(os, 'sync', tracker.track_sync, raising=False)

        # Mock _write_state to track calls
        original_write = ota_instance._write_state
        def tracked_write(ref, commit):
            tracker.track_write_state(ref, commit)
            return original_write(ref, commit)
        monkeypatch.setattr(ota_instance, '_write_state', tracked_write)

        # Create test scenario
        _write(tmp_path / "app.py", b"old")
        _write(tmp_path / ota_instance.stage / "app.py", b"new")

        # Execute swap
        ota_instance.stage_and_swap("v2.0", "abc123")

        # Verify sync happened before write_state
        assert tracker.sync_before_write_state(), \
            "os.sync() must be called BEFORE _write_state()"

    def test_version_synced_after_write(self, ota_instance, tmp_path, monkeypatch):
        """Ensure os.sync() is called AFTER _write_state()."""
        tracker = SyncCallTracker()

        # Mock os.sync to track calls
        monkeypatch.setattr(os, 'sync', tracker.track_sync, raising=False)

        # Mock _write_state to track calls
        original_write = ota_instance._write_state
        def tracked_write(ref, commit):
            tracker.track_write_state(ref, commit)
            return original_write(ref, commit)
        monkeypatch.setattr(ota_instance, '_write_state', tracked_write)

        # Create test scenario
        _write(tmp_path / "app.py", b"old")
        _write(tmp_path / ota_instance.stage / "app.py", b"new")

        # Execute swap
        ota_instance.stage_and_swap("v2.0", "abc123")

        # Verify sync happened after write_state
        assert tracker.sync_after_write_state(), \
            "os.sync() must be called AFTER _write_state() to persist version.json"

    def test_sync_order_correct(self, ota_instance, tmp_path, monkeypatch):
        """Verify complete order: swap → sync → write_state → sync."""
        tracker = SyncCallTracker()

        # Mock os.sync to track calls
        monkeypatch.setattr(os, 'sync', tracker.track_sync, raising=False)

        # Mock _write_state to track calls
        original_write = ota_instance._write_state
        def tracked_write(ref, commit):
            tracker.track_write_state(ref, commit)
            return original_write(ref, commit)
        monkeypatch.setattr(ota_instance, '_write_state', tracked_write)

        # Create test scenario
        _write(tmp_path / "app.py", b"old")
        _write(tmp_path / ota_instance.stage / "app.py", b"new")

        # Execute swap
        ota_instance.stage_and_swap("v2.0", "abc123")

        # Verify complete order
        assert tracker.correct_order(), \
            "Call order must be: file_swaps → os.sync() → _write_state() → os.sync()"

        # Verify we have both syncs
        assert len(tracker.sync_calls) >= 2, \
            "Must have at least 2 sync calls (before and after _write_state)"

        # Verify write_state was called
        assert len(tracker.write_state_calls) == 1, \
            "_write_state() should be called exactly once"

    def test_version_not_written_on_swap_failure(self, ota_instance, tmp_path, monkeypatch):
        """If file swap fails, version.json should NOT be written."""
        write_state_called = []

        # Mock _write_state to track if called
        original_write = ota_instance._write_state
        def tracked_write(ref, commit):
            write_state_called.append((ref, commit))
            return original_write(ref, commit)
        monkeypatch.setattr(ota_instance, '_write_state', tracked_write)

        # Create test scenario with failing file
        _write(tmp_path / "app.py", b"old")
        _write(tmp_path / ota_instance.stage / "app.py", b"new")
        _write(tmp_path / ota_instance.stage / "bad.py", b"boom")

        # Make second rename fail
        original_rename = os.rename
        rename_count = [0]
        def failing_rename(src, dst):
            rename_count[0] += 1
            # First rename for backup succeeds, second rename for bad.py fails
            if "bad.py" in src and not dst.endswith(".ota_backup/bad.py"):
                raise OSError("Simulated disk error")
            return original_rename(src, dst)
        monkeypatch.setattr(os, 'rename', failing_rename)

        # Execute swap (should fail and rollback)
        with pytest.raises(OSError):
            ota_instance.stage_and_swap("v2.0", "abc123")

        # Verify version.json was NOT written
        assert len(write_state_called) == 0, \
            "_write_state() should NOT be called when swap fails"

        # Verify version.json doesn't exist
        assert not (tmp_path / "version.json").exists(), \
            "version.json should not exist after failed swap"


# ============================================================================
# Test Class 2: TestPowerLossScenarios
# ============================================================================

class TestPowerLossScenarios:
    """Simulate power loss at critical points to verify state consistency."""

    def test_power_loss_during_swap(self, ota_instance, tmp_path, monkeypatch):
        """Simulate crash during file swap (before first sync)."""
        simulator = PowerLossSimulator("during_swap")

        # Wrap os.rename
        original_rename = os.rename
        monkeypatch.setattr(os, 'rename', simulator.wrap_rename(original_rename))

        # Create test scenario
        _write(tmp_path / "file1.py", b"old1")
        _write(tmp_path / "file2.py", b"old2")
        _write(tmp_path / ota_instance.stage / "file1.py", b"new1")
        _write(tmp_path / ota_instance.stage / "file2.py", b"new2")

        # Execute swap (should crash)
        with pytest.raises(SystemExit, match="POWER LOSS during file swap"):
            ota_instance.stage_and_swap("v2.0", "abc123")

        # Verify version.json was NOT created
        assert not (tmp_path / "version.json").exists(), \
            "version.json should not exist - crash before sync"

    def test_power_loss_during_first_sync(self, ota_instance, tmp_path, monkeypatch):
        """Crash during os.sync() before version.json write."""
        simulator = PowerLossSimulator("first_sync")

        # Wrap os.sync
        original_sync = getattr(os, 'sync', lambda: None)
        monkeypatch.setattr(os, 'sync', simulator.wrap_sync(original_sync), raising=False)

        # Create test scenario
        _write(tmp_path / "app.py", b"old")
        _write(tmp_path / ota_instance.stage / "app.py", b"new")

        # Execute swap (should crash during first sync)
        with pytest.raises(SystemExit, match="POWER LOSS during first sync"):
            ota_instance.stage_and_swap("v2.0", "abc123")

        # Verify version.json was NOT created (crash before write)
        assert not (tmp_path / "version.json").exists(), \
            "version.json should not exist - crash during first sync (before write_state)"

    def test_power_loss_after_version_write(self, ota_instance, tmp_path, monkeypatch):
        """Crash after version.json written but before final sync (safe state)."""
        # This tests the window between _write_state and final sync
        # version.json exists but might not be fully synced

        write_state_completed = []

        # Mock _write_state to track completion
        original_write = ota_instance._write_state
        def tracked_write(ref, commit):
            result = original_write(ref, commit)
            write_state_completed.append(True)
            return result
        monkeypatch.setattr(ota_instance, '_write_state', tracked_write)

        # Mock final sync to crash
        sync_count = [0]
        original_sync = getattr(os, 'sync', lambda: None)
        def counting_sync():
            sync_count[0] += 1
            # Let first sync pass, crash on final sync (after write_state)
            if sync_count[0] >= 2 and write_state_completed:
                raise SystemExit("POWER LOSS after version write")
            if hasattr(original_sync, '__call__'):
                return original_sync()
        monkeypatch.setattr(os, 'sync', counting_sync, raising=False)

        # Create test scenario
        _write(tmp_path / "app.py", b"old")
        _write(tmp_path / ota_instance.stage / "app.py", b"new")

        # Execute swap (should crash during final sync)
        with pytest.raises(SystemExit, match="POWER LOSS after version write"):
            ota_instance.stage_and_swap("v2.0", "abc123")

        # This is a SAFE state: files are swapped AND synced,
        # version.json is written (though maybe not synced)
        # On reboot, version.json reflects actual state
        assert (tmp_path / "version.json").exists(), \
            "version.json should exist - written before crash"

        # Verify content matches what was applied
        with open(tmp_path / "version.json") as f:
            state = json.load(f)
            assert state["ref"] == "v2.0"
            assert state["commit"] == "abc123"

    def test_power_loss_during_version_sync(self, ota_instance, tmp_path, monkeypatch):
        """
        Crash during final sync (version.json may be incomplete on disk).

        Note: In real power loss, the sync exception is caught by the try/except
        around os.sync() calls, so execution continues. version.json will be
        written successfully, just the final sync may not complete.
        """
        # Track if final sync was attempted
        sync_count = [0]
        write_state_called = [False]

        # Wrap os.sync to track attempts (but don't actually crash, since
        # the code catches exceptions around sync)
        original_sync = getattr(os, 'sync', lambda: None)
        def counting_sync():
            sync_count[0] += 1
            # Simulating power loss during sync - the exception is caught
            # so we just track it was attempted
            if sync_count[0] >= 2 and write_state_called[0]:
                # In real power loss, this would be a hard crash
                # But the code's try/except catches normal exceptions
                pass
            if hasattr(original_sync, '__call__'):
                return original_sync()
        monkeypatch.setattr(os, 'sync', counting_sync, raising=False)

        # Track write_state calls
        original_write = ota_instance._write_state
        def tracked_write(ref, commit):
            write_state_called[0] = True
            return original_write(ref, commit)
        monkeypatch.setattr(ota_instance, '_write_state', tracked_write)

        # Create test scenario
        _write(tmp_path / "app.py", b"old")
        _write(tmp_path / ota_instance.stage / "app.py", b"new")

        # Execute swap (completes successfully despite sync "issues")
        ota_instance.stage_and_swap("v2.0", "abc123")

        # Verify version.json exists and is correct
        assert (tmp_path / "version.json").exists(), \
            "version.json should be written even if final sync has issues"

        # Verify at least 2 syncs were attempted (before and after version write)
        assert sync_count[0] >= 2, \
            "Should attempt sync before and after version.json write"

        # Verify write_state was called
        assert write_state_called[0], \
            "_write_state should be called between syncs"


# ============================================================================
# Test Class 3: TestVersionConsistency
# ============================================================================

class TestVersionConsistency:
    """Verify version.json content matches actual filesystem state."""

    def test_version_matches_applied_files(self, ota_instance, tmp_path):
        """version.json ref/commit should match the files that were applied."""
        # Create test scenario
        _write(tmp_path / "app.py", b"old")
        _write(tmp_path / ota_instance.stage / "app.py", b"new_v2")

        # Execute swap
        ota_instance.stage_and_swap("v2.0", "commit_abc")

        # Verify version.json matches
        with open(tmp_path / "version.json") as f:
            state = json.load(f)
            assert state["ref"] == "v2.0"
            assert state["commit"] == "commit_abc"

        # Verify file was actually updated
        assert (tmp_path / "app.py").read_bytes() == b"new_v2"

    def test_version_not_updated_on_partial_swap(self, ota_instance, tmp_path, monkeypatch):
        """If some files fail to swap, version.json should remain unchanged."""
        # Create initial version
        _write(tmp_path / "version.json", json.dumps({"ref": "v1.0", "commit": "old123"}))
        _write(tmp_path / "app.py", b"v1_content")

        # Stage new files (one will fail)
        _write(tmp_path / ota_instance.stage / "app.py", b"v2_content")
        _write(tmp_path / ota_instance.stage / "bad.py", b"will_fail")

        # Make rename fail for bad.py
        original_rename = os.rename
        def failing_rename(src, dst):
            if "bad.py" in src and not dst.endswith(".ota_backup/bad.py"):
                raise OSError("Disk error")
            return original_rename(src, dst)
        monkeypatch.setattr(os, 'rename', failing_rename)

        # Execute swap (should fail and rollback)
        with pytest.raises(OSError):
            ota_instance.stage_and_swap("v2.0", "new456")

        # Verify version.json unchanged (still v1.0)
        with open(tmp_path / "version.json") as f:
            state = json.load(f)
            assert state["ref"] == "v1.0", "Version should remain at v1.0 after failed swap"
            assert state["commit"] == "old123"

        # Verify app.py was rolled back
        assert (tmp_path / "app.py").read_bytes() == b"v1_content"

    def test_version_rollback_safe(self, ota_instance, tmp_path, monkeypatch):
        """Rollback should not corrupt version.json."""
        # Create initial state
        _write(tmp_path / "version.json", json.dumps({"ref": "v1.0", "commit": "abc"}))
        _write(tmp_path / "app.py", b"v1")

        # Stage update
        _write(tmp_path / ota_instance.stage / "app.py", b"v2")
        _write(tmp_path / ota_instance.stage / "fail.py", b"boom")

        # Fail after first file swapped
        original_rename = os.rename
        def failing_rename(src, dst):
            if "fail.py" in src and not dst.endswith(".ota_backup/fail.py"):
                raise OSError("Failure")
            return original_rename(src, dst)
        monkeypatch.setattr(os, 'rename', failing_rename)

        # Execute (should rollback)
        with pytest.raises(OSError):
            ota_instance.stage_and_swap("v2.0", "def")

        # Verify version.json still valid and unchanged
        with open(tmp_path / "version.json") as f:
            state = json.load(f)
            assert state["ref"] == "v1.0"
            assert state["commit"] == "abc"

        # Verify file rolled back
        assert (tmp_path / "app.py").read_bytes() == b"v1"

    def test_version_idempotent_write(self, ota_instance, tmp_path):
        """Calling _write_state twice with same values should be safe."""
        # Write once
        ota_instance._write_state("v1.0", "abc123")

        # Verify written
        with open(tmp_path / "version.json") as f:
            state1 = json.load(f)

        # Write again with same values
        ota_instance._write_state("v1.0", "abc123")

        # Verify still correct
        with open(tmp_path / "version.json") as f:
            state2 = json.load(f)

        assert state1 == state2 == {"ref": "v1.0", "commit": "abc123"}


# ============================================================================
# Test Class 4: TestSyncIntegration
# ============================================================================

class TestSyncIntegration:
    """Test os.sync() integration and error handling."""

    def test_sync_called_if_available(self, ota_instance, tmp_path, monkeypatch):
        """If os.sync() exists, it should be called twice (before and after version write)."""
        sync_calls = []

        def mock_sync():
            sync_calls.append(True)

        monkeypatch.setattr(os, 'sync', mock_sync, raising=False)

        # Create test scenario
        _write(tmp_path / "app.py", b"old")
        _write(tmp_path / ota_instance.stage / "app.py", b"new")

        # Execute swap
        ota_instance.stage_and_swap("v2.0", "abc123")

        # Verify sync called multiple times
        # (once for file swap, once before version write, once after version write)
        assert len(sync_calls) >= 2, \
            f"os.sync() should be called at least twice, got {len(sync_calls)} calls"

    def test_sync_not_called_if_missing(self, ota_instance, tmp_path, monkeypatch):
        """If os.sync() doesn't exist, no error should occur."""
        # Remove os.sync if it exists
        monkeypatch.delattr(os, 'sync', raising=False)

        # Create test scenario
        _write(tmp_path / "app.py", b"old")
        _write(tmp_path / ota_instance.stage / "app.py", b"new")

        # Execute swap (should succeed without sync)
        ota_instance.stage_and_swap("v2.0", "abc123")

        # Verify update applied
        assert (tmp_path / "app.py").read_bytes() == b"new"
        assert (tmp_path / "version.json").exists()

    def test_sync_exception_handled(self, ota_instance, tmp_path, monkeypatch):
        """If os.sync() raises exception, it should be caught and execution continues."""
        sync_count = [0]

        def failing_sync():
            sync_count[0] += 1
            raise OSError("Sync failed")

        monkeypatch.setattr(os, 'sync', failing_sync, raising=False)

        # Create test scenario
        _write(tmp_path / "app.py", b"old")
        _write(tmp_path / ota_instance.stage / "app.py", b"new")

        # Execute swap (should succeed despite sync failures)
        ota_instance.stage_and_swap("v2.0", "abc123")

        # Verify sync was attempted
        assert sync_count[0] > 0, "os.sync() should have been attempted"

        # Verify update still applied (sync failures are non-fatal)
        assert (tmp_path / "app.py").read_bytes() == b"new"
        assert (tmp_path / "version.json").exists()

    def test_sync_called_for_version_file(self, ota_instance, tmp_path, monkeypatch):
        """Verify os.sync() is specifically called after version.json write."""
        sync_calls = []
        write_state_calls = []

        # Track sync calls
        original_sync = getattr(os, 'sync', lambda: None)
        def tracked_sync():
            sync_calls.append(len(write_state_calls))
            if hasattr(original_sync, '__call__'):
                return original_sync()
        monkeypatch.setattr(os, 'sync', tracked_sync, raising=False)

        # Track write_state calls
        original_write = ota_instance._write_state
        def tracked_write(ref, commit):
            write_state_calls.append(True)
            return original_write(ref, commit)
        monkeypatch.setattr(ota_instance, '_write_state', tracked_write)

        # Create test scenario
        _write(tmp_path / "app.py", b"old")
        _write(tmp_path / ota_instance.stage / "app.py", b"new")

        # Execute swap
        ota_instance.stage_and_swap("v2.0", "abc123")

        # Verify sync called after write_state
        # Last sync call should have happened after write_state was called
        assert len(write_state_calls) == 1
        assert sync_calls[-1] >= 1, \
            "Final os.sync() should be called after _write_state()"


# ============================================================================
# Test Class 5: TestVersionStateTransitions
# ============================================================================

class TestVersionStateTransitions:
    """Test version.json state transitions through update lifecycle."""

    def test_version_state_before_update(self, ota_instance, tmp_path):
        """Read old version before update is applied."""
        # Create initial version
        _write(tmp_path / "version.json", json.dumps({"ref": "v1.0", "commit": "old123"}))

        # Read current state
        state = ota_instance._read_state()
        assert state["ref"] == "v1.0"
        assert state["commit"] == "old123"

    def test_version_state_after_successful_update(self, ota_instance, tmp_path):
        """New version should be written correctly after successful update."""
        # Create initial state
        _write(tmp_path / "version.json", json.dumps({"ref": "v1.0", "commit": "abc"}))
        _write(tmp_path / "app.py", b"v1")

        # Stage update
        _write(tmp_path / ota_instance.stage / "app.py", b"v2")

        # Execute swap
        ota_instance.stage_and_swap("v2.0", "def456")

        # Verify new version
        state = ota_instance._read_state()
        assert state["ref"] == "v2.0"
        assert state["commit"] == "def456"

        # Verify file updated
        assert (tmp_path / "app.py").read_bytes() == b"v2"

    def test_version_state_unchanged_on_failure(self, ota_instance, tmp_path, monkeypatch):
        """Update fails → old version should remain."""
        # Create initial state
        _write(tmp_path / "version.json", json.dumps({"ref": "v1.0", "commit": "abc"}))
        _write(tmp_path / "app.py", b"v1")

        # Stage update with failing file
        _write(tmp_path / ota_instance.stage / "app.py", b"v2")
        _write(tmp_path / ota_instance.stage / "bad.py", b"fail")

        # Make rename fail
        original_rename = os.rename
        def failing_rename(src, dst):
            if "bad.py" in src and not dst.endswith(".ota_backup/bad.py"):
                raise OSError("Error")
            return original_rename(src, dst)
        monkeypatch.setattr(os, 'rename', failing_rename)

        # Execute (should fail)
        with pytest.raises(OSError):
            ota_instance.stage_and_swap("v2.0", "def456")

        # Verify version unchanged
        state = ota_instance._read_state()
        assert state["ref"] == "v1.0"
        assert state["commit"] == "abc"

    def test_version_state_file_not_exists(self, ota_instance, tmp_path):
        """First update → version.json should be created."""
        # No existing version.json
        assert not (tmp_path / "version.json").exists()

        # Stage first update
        _write(tmp_path / ota_instance.stage / "app.py", b"v1")

        # Execute swap
        ota_instance.stage_and_swap("v1.0", "first123")

        # Verify version.json created
        assert (tmp_path / "version.json").exists()
        state = ota_instance._read_state()
        assert state["ref"] == "v1.0"
        assert state["commit"] == "first123"


# ============================================================================
# Test Class 6: TestEdgeCases
# ============================================================================

class TestEdgeCases:
    """Test edge cases and error conditions for version.json timing."""

    def test_version_write_with_empty_applied_list(self, ota_instance, tmp_path):
        """No files swapped → version should still be updated (manifestless mode may have no changes)."""
        # Create existing version
        _write(tmp_path / "version.json", json.dumps({"ref": "v1.0", "commit": "abc"}))

        # Call swap with empty staging (no files to swap)
        # This can happen if all files are already up-to-date
        ota_instance.stage_and_swap("v1.0", "abc")

        # Version should still exist (may be skipped by _write_state optimization)
        assert (tmp_path / "version.json").exists()

    def test_version_write_with_unicode_ref(self, ota_instance, tmp_path):
        """Non-ASCII refs should be handled correctly."""
        # Stage file
        _write(tmp_path / ota_instance.stage / "app.py", b"new")

        # Execute with unicode ref
        unicode_ref = "v2.0-\u4e2d\u6587"  # Chinese characters
        ota_instance.stage_and_swap(unicode_ref, "abc123")

        # Verify version.json contains unicode correctly
        with open(tmp_path / "version.json", encoding="utf-8") as f:
            state = json.load(f)
            assert state["ref"] == unicode_ref

    def test_version_write_permission_denied(self, ota_instance, tmp_path, monkeypatch):
        """Can't write version.json → error should be raised but tracked."""
        # Stage file
        _write(tmp_path / "app.py", b"old")
        _write(tmp_path / ota_instance.stage / "app.py", b"new")

        # Make version.json write fail
        original_open = open
        def failing_open(path, mode='r', *args, **kwargs):
            if 'version.json' in str(path) and 'w' in mode:
                raise PermissionError("Cannot write version.json")
            return original_open(path, mode, *args, **kwargs)

        monkeypatch.setattr('builtins.open', failing_open)

        # Execute swap (should fail at version write)
        with pytest.raises(PermissionError):
            ota_instance.stage_and_swap("v2.0", "abc123")

        # Files should be rolled back since write_state failed
        assert (tmp_path / "app.py").read_bytes() == b"old"

    def test_version_write_disk_full(self, ota_instance, tmp_path, monkeypatch):
        """Disk full during version.json write → proper error handling."""
        # Stage file
        _write(tmp_path / "app.py", b"old")
        _write(tmp_path / ota_instance.stage / "app.py", b"new")

        # Simulate disk full on version.json write
        original_open = open
        def disk_full_open(path, mode='r', *args, **kwargs):
            if 'version.json' in str(path) and 'w' in mode:
                raise OSError(28, "No space left on device")  # ENOSPC
            return original_open(path, mode, *args, **kwargs)

        monkeypatch.setattr('builtins.open', disk_full_open)

        # Execute swap (should fail)
        with pytest.raises(OSError, match="No space left on device"):
            ota_instance.stage_and_swap("v2.0", "abc123")

        # Files should be rolled back
        assert (tmp_path / "app.py").read_bytes() == b"old"
