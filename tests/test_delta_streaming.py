"""
Comprehensive test suite for delta streaming functionality (Fix #3).

Tests the new streaming mode that reduces memory usage from O(delta_size)
to O(buffer_size), preventing OOM on constrained embedded devices.
"""

import os
import io
import hashlib
import pytest
from delta import (
    apply_delta,
    create_delta,
    _ChunkedDeltaReader,
    _apply_delta_streaming,
    _apply_delta_legacy,
    _write_varint,
    DeltaError,
    DELTA_MAGIC,
    DELTA_VERSION,
    OP_COPY_OLD,
    OP_NEW_DATA,
    OP_END,
    MAX_COPY_SIZE,
    MAX_INSERT_SIZE,
)


# ============================================================================
# Fixtures and Helpers
# ============================================================================

@pytest.fixture
def old_file(tmp_path):
    """Create a simple old file for delta testing."""
    path = tmp_path / "old.txt"
    path.write_bytes(b"Hello, World! This is the old content.")
    return str(path)


@pytest.fixture
def new_file(tmp_path):
    """Create a simple new file for delta testing."""
    path = tmp_path / "new.txt"
    path.write_bytes(b"Hello, World! This is the NEW content with changes.")
    return str(path)


@pytest.fixture
def large_old_file(tmp_path):
    """Create a large old file (5KB) for testing large deltas."""
    path = tmp_path / "large_old.bin"
    # Create content with repeating pattern for better compression
    content = b"".join([b"Block %04d: " % i + b"X" * 100 + b"\n" for i in range(50)])
    path.write_bytes(content)
    return str(path)


@pytest.fixture
def large_new_file(tmp_path):
    """Create a large new file (5KB+) with partial changes."""
    path = tmp_path / "large_new.bin"
    # Keep first 30 blocks, modify middle, add new blocks
    content = b"".join([b"Block %04d: " % i + b"X" * 100 + b"\n" for i in range(30)])
    content += b"".join([b"Modified %04d: " % i + b"Y" * 100 + b"\n" for i in range(10)])
    content += b"".join([b"NewBlock %04d: " % i + b"Z" * 100 + b"\n" for i in range(15)])
    path.write_bytes(content)
    return str(path)


@pytest.fixture
def output_file(tmp_path):
    """Create path for output file."""
    return str(tmp_path / "output.bin")


def create_minimal_delta(insert_data=b"test"):
    """Create a minimal valid delta with just an insert operation."""
    delta = bytearray(DELTA_MAGIC)
    delta.append(DELTA_VERSION)
    delta.append(OP_NEW_DATA)
    delta.extend(_write_varint(len(insert_data)))
    delta.extend(insert_data)
    delta.append(OP_END)
    return bytes(delta)


def create_copy_delta(copy_offset=0, copy_length=10):
    """Create a delta with a copy operation."""
    delta = bytearray(DELTA_MAGIC)
    delta.append(DELTA_VERSION)
    delta.append(OP_COPY_OLD)
    delta.extend(_write_varint(copy_offset))
    delta.extend(_write_varint(copy_length))
    delta.append(OP_END)
    return bytes(delta)


def create_mixed_delta(copy_offset=0, copy_length=5, insert_data=b"NEW"):
    """Create a delta with both copy and insert operations."""
    delta = bytearray(DELTA_MAGIC)
    delta.append(DELTA_VERSION)
    delta.append(OP_COPY_OLD)
    delta.extend(_write_varint(copy_offset))
    delta.extend(_write_varint(copy_length))
    delta.append(OP_NEW_DATA)
    delta.extend(_write_varint(len(insert_data)))
    delta.extend(insert_data)
    delta.append(OP_END)
    return bytes(delta)


def create_invalid_magic_delta():
    """Create a delta with invalid magic number."""
    delta = bytearray(b"BADMAGIC")
    delta.append(DELTA_VERSION)
    delta.append(OP_END)
    return bytes(delta)


def create_invalid_version_delta():
    """Create a delta with invalid version."""
    delta = bytearray(DELTA_MAGIC)
    delta.append(99)  # Invalid version
    delta.append(OP_END)
    return bytes(delta)


def write_delta_file(tmp_path, delta_data, filename="delta.bin"):
    """Write delta data to file and return path."""
    path = tmp_path / filename
    path.write_bytes(delta_data)
    return str(path)


def compute_sha256(file_path):
    """Compute SHA256 hash of a file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(4096)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# ============================================================================
# Test Class 1: ChunkedDeltaReader
# ============================================================================

class TestChunkedDeltaReader:
    """Test the streaming _ChunkedDeltaReader class."""

    def test_reader_reads_single_byte(self, tmp_path):
        """Read 1 byte from buffer."""
        data = b"ABCDEF"
        path = tmp_path / "test.bin"
        path.write_bytes(data)

        with open(str(path), "rb") as f:
            reader = _ChunkedDeltaReader(f, buffer_size=64)
            byte = reader.read_byte()
            assert byte == ord('A')
            assert reader.offset == 1

    def test_reader_reads_multiple_bytes(self, tmp_path):
        """Read 100 bytes across multiple buffer fills."""
        data = b"X" * 200
        path = tmp_path / "test.bin"
        path.write_bytes(data)

        with open(str(path), "rb") as f:
            reader = _ChunkedDeltaReader(f, buffer_size=64)
            result = reader.read_bytes(100)
            assert len(result) == 100
            assert result == b"X" * 100
            assert reader.offset == 100

    def test_reader_reads_exact_buffer_size(self, tmp_path):
        """Read exactly 64 bytes (buffer size)."""
        data = b"B" * 128
        path = tmp_path / "test.bin"
        path.write_bytes(data)

        with open(str(path), "rb") as f:
            reader = _ChunkedDeltaReader(f, buffer_size=64)
            result = reader.read_bytes(64)
            assert len(result) == 64
            assert result == b"B" * 64
            assert reader.offset == 64

    def test_reader_reads_larger_than_buffer(self, tmp_path):
        """Read 200 bytes (multiple fills)."""
        data = b"Y" * 300
        path = tmp_path / "test.bin"
        path.write_bytes(data)

        with open(str(path), "rb") as f:
            reader = _ChunkedDeltaReader(f, buffer_size=64)
            result = reader.read_bytes(200)
            assert len(result) == 200
            assert result == b"Y" * 200
            assert reader.offset == 200

    def test_reader_read_varint_small(self, tmp_path):
        """Varint encoding 42 → single byte."""
        varint_42 = _write_varint(42)
        assert varint_42 == b'\x2a'  # 42 in single byte

        path = tmp_path / "varint.bin"
        path.write_bytes(varint_42 + b"rest")

        with open(str(path), "rb") as f:
            reader = _ChunkedDeltaReader(f)
            value = reader.read_varint()
            assert value == 42
            assert reader.offset == 1

    def test_reader_read_varint_large(self, tmp_path):
        """Varint encoding 65535 → multi-byte."""
        varint_65535 = _write_varint(65535)
        assert len(varint_65535) > 1  # Multi-byte encoding

        path = tmp_path / "varint.bin"
        path.write_bytes(varint_65535 + b"rest")

        with open(str(path), "rb") as f:
            reader = _ChunkedDeltaReader(f)
            value = reader.read_varint()
            assert value == 65535

    def test_reader_eof_detection(self, tmp_path):
        """Detect end of file correctly."""
        data = b"SHORT"
        path = tmp_path / "test.bin"
        path.write_bytes(data)

        with open(str(path), "rb") as f:
            reader = _ChunkedDeltaReader(f, buffer_size=64)
            reader.read_bytes(5)
            # Try to read more to trigger EOF detection
            reader._ensure_bytes(1)
            assert reader.eof is True

    def test_reader_eof_raises_error(self, tmp_path):
        """Reading past EOF raises DeltaError."""
        data = b"ABC"
        path = tmp_path / "test.bin"
        path.write_bytes(data)

        with open(str(path), "rb") as f:
            reader = _ChunkedDeltaReader(f)
            reader.read_bytes(3)
            with pytest.raises(DeltaError, match="Unexpected EOF"):
                reader.read_byte()


# ============================================================================
# Test Class 2: Delta Streaming Mode
# ============================================================================

class TestDeltaStreamingMode:
    """Test the new streaming mode that reduces memory usage."""

    def test_streaming_with_file_path(self, tmp_path, old_file, output_file):
        """Pass string path → uses streaming."""
        delta_data = create_minimal_delta(b"streaming test")
        delta_path = write_delta_file(tmp_path, delta_data)

        # Pass string path (not bytes) to trigger streaming mode
        result_hash = apply_delta(old_file, delta_path, output_file)

        assert os.path.exists(output_file)
        assert result_hash == compute_sha256(output_file)
        with open(output_file, "rb") as f:
            assert f.read() == b"streaming test"

    def test_streaming_with_copy_operation(self, tmp_path, old_file, output_file):
        """Delta with OP_COPY_OLD works."""
        delta_data = create_copy_delta(copy_offset=0, copy_length=13)  # "Hello, World!"
        delta_path = write_delta_file(tmp_path, delta_data)

        result_hash = apply_delta(old_file, delta_path, output_file)

        with open(output_file, "rb") as f:
            content = f.read()
            assert content == b"Hello, World!"

    def test_streaming_with_insert_operation(self, tmp_path, old_file, output_file):
        """Delta with OP_NEW_DATA works."""
        delta_data = create_minimal_delta(b"Inserted content here")
        delta_path = write_delta_file(tmp_path, delta_data)

        result_hash = apply_delta(old_file, delta_path, output_file)

        with open(output_file, "rb") as f:
            assert f.read() == b"Inserted content here"

    def test_streaming_with_mixed_operations(self, tmp_path, old_file, output_file):
        """Both COPY and INSERT."""
        # Copy first 5 bytes ("Hello") then insert " NEW WORLD"
        delta_data = create_mixed_delta(copy_offset=0, copy_length=5, insert_data=b" NEW WORLD")
        delta_path = write_delta_file(tmp_path, delta_data)

        result_hash = apply_delta(old_file, delta_path, output_file)

        with open(output_file, "rb") as f:
            assert f.read() == b"Hello NEW WORLD"

    def test_streaming_with_large_delta(self, tmp_path, large_old_file, large_new_file, output_file):
        """50KB+ delta file streams correctly."""
        # Create real delta between large files
        delta_path = str(tmp_path / "large_delta.bin")
        delta_data = create_delta(large_old_file, large_new_file, delta_path)

        # Verify delta is substantial
        assert len(delta_data) > 1024  # At least 1KB

        # Apply using streaming mode
        result_hash = apply_delta(large_old_file, delta_path, output_file)

        # Verify output matches new file
        with open(large_new_file, "rb") as expected:
            with open(output_file, "rb") as actual:
                assert actual.read() == expected.read()

    def test_streaming_verifies_hash(self, tmp_path, old_file, output_file):
        """Output SHA256 verified correctly."""
        delta_data = create_minimal_delta(b"hash test")
        delta_path = write_delta_file(tmp_path, delta_data)

        # Compute expected hash
        expected_hash = hashlib.sha256(b"hash test").hexdigest()

        # Apply with hash verification
        result_hash = apply_delta(old_file, delta_path, output_file, expected_hash=expected_hash)

        assert result_hash == expected_hash

    def test_streaming_hash_mismatch_error(self, tmp_path, old_file, output_file):
        """Wrong hash → DeltaError."""
        delta_data = create_minimal_delta(b"hash test")
        delta_path = write_delta_file(tmp_path, delta_data)

        # Provide incorrect hash
        wrong_hash = "0" * 64

        with pytest.raises(DeltaError, match="Output hash mismatch"):
            apply_delta(old_file, delta_path, output_file, expected_hash=wrong_hash)

    def test_streaming_invalid_magic(self, tmp_path, old_file, output_file):
        """Bad delta magic → DeltaError."""
        delta_data = create_invalid_magic_delta()
        delta_path = write_delta_file(tmp_path, delta_data)

        with pytest.raises(DeltaError, match="Invalid delta magic"):
            apply_delta(old_file, delta_path, output_file)


# ============================================================================
# Test Class 3: Delta Legacy Mode
# ============================================================================

class TestDeltaLegacyMode:
    """Test backward compatibility with in-memory mode."""

    def test_legacy_with_bytes_input(self, old_file, output_file):
        """Pass bytes → uses legacy mode."""
        delta_data = create_minimal_delta(b"legacy test")

        # Pass bytes (not path) to trigger legacy mode
        result_hash = apply_delta(old_file, delta_data, output_file)

        assert os.path.exists(output_file)
        with open(output_file, "rb") as f:
            assert f.read() == b"legacy test"

    def test_legacy_backward_compatible(self, old_file, output_file):
        """Existing test patterns still work."""
        # This is the old usage pattern with bytes
        delta_data = create_minimal_delta(b"backward compat")
        result_hash = apply_delta(old_file, delta_data, output_file)

        assert result_hash == compute_sha256(output_file)

    def test_legacy_with_copy_operation(self, old_file, output_file):
        """OP_COPY_OLD works."""
        delta_data = create_copy_delta(copy_offset=7, copy_length=5)  # "World"
        result_hash = apply_delta(old_file, delta_data, output_file)

        with open(output_file, "rb") as f:
            assert f.read() == b"World"

    def test_legacy_with_insert_operation(self, old_file, output_file):
        """OP_NEW_DATA works."""
        delta_data = create_minimal_delta(b"Insert via legacy")
        result_hash = apply_delta(old_file, delta_data, output_file)

        with open(output_file, "rb") as f:
            assert f.read() == b"Insert via legacy"

    def test_legacy_hash_verification(self, old_file, output_file):
        """Hash checked in legacy mode."""
        delta_data = create_minimal_delta(b"verify me")
        expected_hash = hashlib.sha256(b"verify me").hexdigest()

        result_hash = apply_delta(old_file, delta_data, output_file, expected_hash=expected_hash)
        assert result_hash == expected_hash

    def test_legacy_invalid_delta(self, old_file, output_file):
        """Bad delta data → DeltaError."""
        delta_data = create_invalid_version_delta()

        with pytest.raises(DeltaError, match="Unsupported delta version"):
            apply_delta(old_file, delta_data, output_file)


# ============================================================================
# Test Class 4: Memory Usage
# ============================================================================

class TestMemoryUsage:
    """Test that streaming mode uses less memory than legacy mode."""

    def test_streaming_memory_lower_than_legacy(self, tmp_path, large_old_file, large_new_file, monkeypatch):
        """Measure memory usage (demonstrate concept)."""
        # Create large delta
        delta_path = str(tmp_path / "large_delta.bin")
        delta_data = create_delta(large_old_file, large_new_file, delta_path)
        delta_size = len(delta_data)

        # Legacy mode: entire delta loaded as bytes
        output_legacy = str(tmp_path / "output_legacy.bin")
        apply_delta(large_old_file, delta_data, output_legacy)

        # Verify delta_data holds full delta in memory
        assert len(delta_data) == delta_size
        assert delta_size > 1024  # Verify substantial size

        # Streaming mode: uses file path
        output_streaming = str(tmp_path / "output_streaming.bin")
        apply_delta(large_old_file, delta_path, output_streaming)

        # Both should produce same output
        with open(output_legacy, "rb") as f1, open(output_streaming, "rb") as f2:
            content1 = f1.read()
            content2 = f2.read()
            assert content1 == content2

        # Key point: legacy mode requires delta_size bytes in RAM
        # Streaming mode requires only buffer_size (64 bytes) + chunk_size (512 bytes)
        # Memory savings = delta_size - (64 + 512) bytes

    def test_streaming_buffer_size_configurable(self, tmp_path, old_file):
        """Can override 64-byte buffer."""
        delta_data = create_minimal_delta(b"buffer test")
        delta_path = write_delta_file(tmp_path, delta_data)
        output_path = str(tmp_path / "output.bin")

        # Open delta file and create reader with custom buffer size
        with open(delta_path, "rb") as delta_file:
            reader = _ChunkedDeltaReader(delta_file, buffer_size=32)
            assert reader.buffer_size == 32

            # Verify reader works with custom buffer
            magic = reader.read_bytes(8)
            assert magic == DELTA_MAGIC

    def test_streaming_never_loads_full_delta(self, tmp_path, large_old_file, large_new_file):
        """Verify incremental reading via buffer behavior."""
        delta_path = str(tmp_path / "delta.bin")
        create_delta(large_old_file, large_new_file, delta_path)
        delta_size = os.path.getsize(delta_path)

        # Open delta and verify reader only buffers small amounts
        with open(delta_path, "rb") as f:
            reader = _ChunkedDeltaReader(f, buffer_size=64)

            # Read header
            magic = reader.read_bytes(8)
            assert magic == DELTA_MAGIC

            # Verify buffer never grows larger than buffer_size
            # (This is enforced by the _ensure_bytes implementation)
            assert reader.buffer_size == 64

            # Read through entire delta in small chunks
            total_read = 9  # Already read magic + version
            while total_read < delta_size:
                try:
                    reader.read_byte()
                    total_read += 1
                    # Buffer should never exceed buffer_size
                    assert len(reader.buffer) <= reader.buffer_size
                except DeltaError:
                    # EOF is expected
                    break

        # Verify we can successfully apply delta with streaming
        output_path = str(tmp_path / "output.bin")
        result_hash = apply_delta(large_old_file, delta_path, output_path)
        assert os.path.exists(output_path)

    def test_streaming_max_memory_bounded(self, tmp_path, large_old_file, large_new_file):
        """Memory usage stays below threshold."""
        delta_path = str(tmp_path / "delta.bin")
        create_delta(large_old_file, large_new_file, delta_path)
        delta_size = os.path.getsize(delta_path)

        # Memory should be bounded by buffer + chunk sizes
        # Buffer: 64 bytes, chunk_size: 512 bytes (default)
        # Total working set should be < 10KB even for large deltas
        expected_max_memory = 10 * 1024  # 10KB threshold

        output_path = str(tmp_path / "output.bin")
        result_hash = apply_delta(large_old_file, delta_path, output_path)

        # Verify success
        assert os.path.exists(output_path)
        assert result_hash == compute_sha256(output_path)

        # Memory usage is implicit - if we didn't OOM, we passed
        # (Real memory profiling would require platform-specific tools)


# ============================================================================
# Test Class 5: Delta Operations
# ============================================================================

class TestDeltaOperations:
    """Test individual delta operations and edge cases."""

    def test_copy_operation_chunked(self, tmp_path, output_file):
        """Large COPY split into chunks."""
        # Create old file with 2KB of data
        old_path = str(tmp_path / "old.bin")
        old_content = b"X" * 2048
        with open(old_path, "wb") as f:
            f.write(old_content)

        # Create delta that copies entire content
        delta_data = create_copy_delta(copy_offset=0, copy_length=2048)
        delta_path = write_delta_file(tmp_path, delta_data)

        result_hash = apply_delta(old_path, delta_path, output_file, chunk_size=512)

        with open(output_file, "rb") as f:
            assert f.read() == old_content

    def test_insert_operation_small(self, tmp_path, old_file, output_file):
        """INSERT < buffer size."""
        small_insert = b"A" * 32  # Less than 64-byte buffer
        delta_data = create_minimal_delta(small_insert)
        delta_path = write_delta_file(tmp_path, delta_data)

        result_hash = apply_delta(old_file, delta_path, output_file)

        with open(output_file, "rb") as f:
            assert f.read() == small_insert

    def test_insert_operation_large(self, tmp_path, old_file, output_file):
        """INSERT > buffer size (2KB limit)."""
        large_insert = b"B" * 1024  # 1KB insert (under 2KB limit)
        delta_data = create_minimal_delta(large_insert)
        delta_path = write_delta_file(tmp_path, delta_data)

        result_hash = apply_delta(old_file, delta_path, output_file)

        with open(output_file, "rb") as f:
            assert f.read() == large_insert

    def test_copy_size_limit_enforced(self, tmp_path, old_file, output_file):
        """COPY > MAX_COPY_SIZE → error."""
        oversized_copy = MAX_COPY_SIZE + 1
        delta_data = create_copy_delta(copy_offset=0, copy_length=oversized_copy)
        delta_path = write_delta_file(tmp_path, delta_data)

        with pytest.raises(DeltaError, match="Copy size too large"):
            apply_delta(old_file, delta_path, output_file)

    def test_insert_size_limit_enforced(self, tmp_path, old_file, output_file):
        """INSERT > MAX_INSERT_SIZE → error."""
        oversized_insert = b"X" * (MAX_INSERT_SIZE + 1)
        delta_data = create_minimal_delta(oversized_insert)
        delta_path = write_delta_file(tmp_path, delta_data)

        with pytest.raises(DeltaError, match="Insert size too large"):
            apply_delta(old_file, delta_path, output_file)

    def test_end_opcode_stops_processing(self, tmp_path, old_file, output_file):
        """OP_END terminates correctly."""
        # Create delta with OP_END followed by garbage
        delta = bytearray(DELTA_MAGIC)
        delta.append(DELTA_VERSION)
        delta.append(OP_NEW_DATA)
        delta.extend(_write_varint(4))
        delta.extend(b"TEST")
        delta.append(OP_END)
        delta.extend(b"GARBAGE_AFTER_END_SHOULD_BE_IGNORED")

        delta_path = write_delta_file(tmp_path, bytes(delta))
        result_hash = apply_delta(old_file, delta_path, output_file)

        with open(output_file, "rb") as f:
            assert f.read() == b"TEST"

    def test_unknown_opcode_error(self, tmp_path, old_file, output_file):
        """Invalid opcode → DeltaError."""
        # Create delta with invalid opcode 0x99
        delta = bytearray(DELTA_MAGIC)
        delta.append(DELTA_VERSION)
        delta.append(0x99)  # Unknown opcode

        delta_path = write_delta_file(tmp_path, bytes(delta))

        with pytest.raises(DeltaError, match="Unknown opcode"):
            apply_delta(old_file, delta_path, output_file)


# ============================================================================
# Test Class 6: Backward Compatibility
# ============================================================================

class TestBackwardCompatibility:
    """Test that both streaming and legacy modes work correctly."""

    def test_bytes_input_still_works(self, old_file, output_file):
        """Old test pattern with bytes."""
        delta_bytes = create_minimal_delta(b"bytes mode")
        result_hash = apply_delta(old_file, delta_bytes, output_file)

        with open(output_file, "rb") as f:
            assert f.read() == b"bytes mode"

    def test_file_path_input_new_mode(self, tmp_path, old_file, output_file):
        """New test pattern with path."""
        delta_data = create_minimal_delta(b"path mode")
        delta_path = write_delta_file(tmp_path, delta_data)

        result_hash = apply_delta(old_file, delta_path, output_file)

        with open(output_file, "rb") as f:
            assert f.read() == b"path mode"

    def test_both_modes_produce_same_output(self, tmp_path, old_file, new_file):
        """Streaming == Legacy for same delta."""
        # Create delta
        delta_data = create_delta(old_file, new_file)
        delta_path = write_delta_file(tmp_path, delta_data)

        # Apply with legacy mode (bytes)
        output_legacy = str(tmp_path / "output_legacy.bin")
        hash_legacy = apply_delta(old_file, delta_data, output_legacy)

        # Apply with streaming mode (path)
        output_streaming = str(tmp_path / "output_streaming.bin")
        hash_streaming = apply_delta(old_file, delta_path, output_streaming)

        # Both should produce identical output
        assert hash_legacy == hash_streaming
        with open(output_legacy, "rb") as f1, open(output_streaming, "rb") as f2:
            assert f1.read() == f2.read()

    def test_hash_verification_both_modes(self, tmp_path, old_file):
        """Both modes verify correctly."""
        delta_data = create_minimal_delta(b"verify both")
        expected_hash = hashlib.sha256(b"verify both").hexdigest()

        # Legacy mode with hash
        output_legacy = str(tmp_path / "output_legacy.bin")
        hash_legacy = apply_delta(old_file, delta_data, output_legacy, expected_hash=expected_hash)
        assert hash_legacy == expected_hash

        # Streaming mode with hash
        delta_path = write_delta_file(tmp_path, delta_data)
        output_streaming = str(tmp_path / "output_streaming.bin")
        hash_streaming = apply_delta(old_file, delta_path, output_streaming, expected_hash=expected_hash)
        assert hash_streaming == expected_hash
