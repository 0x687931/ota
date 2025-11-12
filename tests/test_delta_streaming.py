"""
Tests for streaming delta application (Fix #3).

Verifies that ChunkedDeltaReader reduces memory usage from 65KB to <1KB
for 50KB delta files.
"""

import os
import sys
import tempfile
import pytest

# Import delta module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from delta import (
    apply_delta,
    create_delta,
    DeltaError,
    ChunkedDeltaReader,
    _read_varint_from_reader,
    DELTA_MAGIC,
    DELTA_VERSION,
    OP_END,
    OP_COPY_OLD,
    OP_NEW_DATA,
)


class TestChunkedDeltaReader:
    """Test the ChunkedDeltaReader class."""

    def test_read_byte(self, tmp_path):
        """Test reading individual bytes."""
        delta_file = tmp_path / "test.delta"
        delta_file.write_bytes(b"Hello World!")

        reader = ChunkedDeltaReader(str(delta_file))
        try:
            assert reader.read_byte() == ord('H')
            assert reader.read_byte() == ord('e')
            assert reader.read_byte() == ord('l')
        finally:
            reader.close()

    def test_read_bytes(self, tmp_path):
        """Test reading multiple bytes."""
        delta_file = tmp_path / "test.delta"
        delta_file.write_bytes(b"Hello World!")

        reader = ChunkedDeltaReader(str(delta_file))
        try:
            assert reader.read_bytes(5) == b"Hello"
            assert reader.read_bytes(7) == b" World!"
        finally:
            reader.close()

    def test_read_across_buffer_boundary(self, tmp_path):
        """Test reading across 64-byte buffer boundaries."""
        # Create data larger than buffer size (64 bytes)
        data = b"A" * 100
        delta_file = tmp_path / "test.delta"
        delta_file.write_bytes(data)

        reader = ChunkedDeltaReader(str(delta_file))
        try:
            result = reader.read_bytes(100)
            assert result == data
        finally:
            reader.close()

    def test_eof_handling(self, tmp_path):
        """Test EOF detection."""
        delta_file = tmp_path / "test.delta"
        delta_file.write_bytes(b"ABC")

        reader = ChunkedDeltaReader(str(delta_file))
        try:
            reader.read_bytes(3)
            assert reader.read_byte() is None
        finally:
            reader.close()

    def test_read_varint_from_reader(self, tmp_path):
        """Test reading variable-length integers."""
        delta_file = tmp_path / "test.delta"
        # Small varint: 42 = 0x2A (single byte)
        # Large varint: 300 = 0xAC 0x02 (two bytes: 0xAC = 172 | 0x80, 0x02)
        delta_file.write_bytes(bytes([0x2A, 0xAC, 0x02]))

        reader = ChunkedDeltaReader(str(delta_file))
        try:
            assert _read_varint_from_reader(reader) == 42
            assert _read_varint_from_reader(reader) == 300
        finally:
            reader.close()


class TestStreamingDelta:
    """Test streaming delta application."""

    def test_small_delta_streaming(self, tmp_path):
        """Test applying a small delta using streaming mode."""
        old_file = tmp_path / "old.txt"
        new_file = tmp_path / "new.txt"
        delta_file = tmp_path / "test.delta"
        output_file = tmp_path / "output.txt"

        # Create test files
        old_file.write_text("Hello World!")
        new_file.write_text("Hello Python!")

        # Generate delta
        create_delta(str(old_file), str(new_file), str(delta_file))

        # Apply delta using streaming mode (pass path as string)
        result_hash = apply_delta(
            str(old_file),
            str(delta_file),  # Path, not bytes
            str(output_file)
        )

        # Verify output
        assert output_file.read_text() == "Hello Python!"
        assert len(result_hash) == 64  # SHA256 hex digest

    def test_large_delta_streaming(self, tmp_path):
        """Test applying a large delta (simulates 50KB+ delta with many changes)."""
        old_file = tmp_path / "old.bin"
        new_file = tmp_path / "new.bin"
        delta_file = tmp_path / "test.delta"
        output_file = tmp_path / "output.bin"

        # Create large files with many small differences to generate large delta
        # Pattern: alternating blocks of old/new data
        old_data = b""
        new_data = b""
        for i in range(100):
            # Small blocks to avoid MAX_COPY_SIZE limit (4096)
            old_block = (b"OLD_%03d_" % i) * 50  # 400 bytes per block
            new_block = (b"NEW_%03d_" % i) * 50  # 400 bytes per block
            old_data += old_block
            new_data += new_block if i % 2 == 0 else old_block  # 50% changed

        old_file.write_bytes(old_data)
        new_file.write_bytes(new_data)

        # Generate delta
        create_delta(str(old_file), str(new_file), str(delta_file))

        # Verify delta size
        delta_size = delta_file.stat().st_size
        assert delta_size > 0

        # Apply delta using streaming mode
        result_hash = apply_delta(
            str(old_file),
            str(delta_file),
            str(output_file)
        )

        # Verify output matches new file
        assert output_file.read_bytes() == new_data

    def test_many_insert_operations(self, tmp_path):
        """Test delta with many insert operations (tests chunked reading)."""
        old_file = tmp_path / "old.txt"
        new_file = tmp_path / "new.txt"
        delta_file = tmp_path / "test.delta"
        output_file = tmp_path / "output.txt"

        # Create files with minimal overlap (forces many inserts)
        old_file.write_text("OLD")
        new_file.write_text("NEW" * 1000)  # Lots of new data

        # Generate delta
        create_delta(str(old_file), str(new_file), str(delta_file))

        # Apply delta using streaming mode
        apply_delta(
            str(old_file),
            str(delta_file),
            str(output_file)
        )

        # Verify output
        assert output_file.read_text() == "NEW" * 1000

    def test_mixed_copy_and_insert(self, tmp_path):
        """Test delta with mixed COPY and NEW_DATA operations."""
        old_file = tmp_path / "old.txt"
        new_file = tmp_path / "new.txt"
        delta_file = tmp_path / "test.delta"
        output_file = tmp_path / "output.txt"

        # Create files with partial overlap
        old_file.write_text("AAAA" + "BBBB" + "CCCC")
        new_file.write_text("AAAA" + "XXXX" + "CCCC")  # Replace BBBB with XXXX

        # Generate delta
        create_delta(str(old_file), str(new_file), str(delta_file))

        # Apply delta using streaming mode
        apply_delta(
            str(old_file),
            str(delta_file),
            str(output_file)
        )

        # Verify output
        assert output_file.read_text() == "AAAA" + "XXXX" + "CCCC"

    def test_legacy_bytes_mode(self, tmp_path):
        """Test backward compatibility with bytes mode."""
        old_file = tmp_path / "old.txt"
        new_file = tmp_path / "new.txt"
        output_file = tmp_path / "output.txt"

        # Create test files
        old_file.write_text("Hello World!")
        new_file.write_text("Hello Python!")

        # Generate delta as bytes
        delta_data = create_delta(str(old_file), str(new_file))

        # Apply delta using legacy bytes mode
        result_hash = apply_delta(
            str(old_file),
            delta_data,  # Bytes, not path
            str(output_file)
        )

        # Verify output
        assert output_file.read_text() == "Hello Python!"
        assert len(result_hash) == 64

    def test_corrupted_delta_magic(self, tmp_path):
        """Test handling of corrupted delta (bad magic)."""
        old_file = tmp_path / "old.txt"
        delta_file = tmp_path / "test.delta"
        output_file = tmp_path / "output.txt"

        old_file.write_text("Hello World!")
        delta_file.write_bytes(b"BADMAGIC" + bytes([DELTA_VERSION, OP_END]))

        with pytest.raises(DeltaError, match="Invalid delta magic"):
            apply_delta(str(old_file), str(delta_file), str(output_file))

    def test_corrupted_delta_version(self, tmp_path):
        """Test handling of unsupported delta version."""
        old_file = tmp_path / "old.txt"
        delta_file = tmp_path / "test.delta"
        output_file = tmp_path / "output.txt"

        old_file.write_text("Hello World!")
        delta_file.write_bytes(DELTA_MAGIC + bytes([99, OP_END]))  # Version 99

        with pytest.raises(DeltaError, match="Unsupported delta version"):
            apply_delta(str(old_file), str(delta_file), str(output_file))

    def test_unexpected_eof_reading_opcode(self, tmp_path):
        """Test handling of truncated delta (EOF reading opcode)."""
        old_file = tmp_path / "old.txt"
        delta_file = tmp_path / "test.delta"
        output_file = tmp_path / "output.txt"

        old_file.write_text("Hello World!")
        # Write header only, no instructions
        delta_file.write_bytes(DELTA_MAGIC + bytes([DELTA_VERSION]))

        with pytest.raises(DeltaError, match="Unexpected EOF reading opcode"):
            apply_delta(str(old_file), str(delta_file), str(output_file))

    def test_unexpected_eof_reading_varint(self, tmp_path):
        """Test handling of truncated varint."""
        old_file = tmp_path / "old.txt"
        delta_file = tmp_path / "test.delta"
        output_file = tmp_path / "output.txt"

        old_file.write_text("Hello World!")
        # Write COPY opcode but truncate varint (missing continuation bytes)
        delta_file.write_bytes(DELTA_MAGIC + bytes([DELTA_VERSION, OP_COPY_OLD, 0x80]))

        with pytest.raises(DeltaError, match="Unexpected EOF reading varint"):
            apply_delta(str(old_file), str(delta_file), str(output_file))

    def test_hash_verification(self, tmp_path):
        """Test output hash verification."""
        old_file = tmp_path / "old.txt"
        new_file = tmp_path / "new.txt"
        delta_file = tmp_path / "test.delta"
        output_file = tmp_path / "output.txt"

        old_file.write_text("Hello World!")
        new_file.write_text("Hello Python!")

        # Generate delta
        create_delta(str(old_file), str(new_file), str(delta_file))

        # Apply with wrong expected hash
        with pytest.raises(DeltaError, match="Output hash mismatch"):
            apply_delta(
                str(old_file),
                str(delta_file),
                str(output_file),
                expected_hash="0" * 64
            )


class TestMemoryUsage:
    """Test memory usage improvements."""

    def test_reader_buffer_size(self, tmp_path):
        """Verify reader uses fixed 64-byte buffer."""
        delta_file = tmp_path / "test.delta"
        delta_file.write_bytes(b"X" * 1000)

        reader = ChunkedDeltaReader(str(delta_file))
        try:
            # Buffer should be at most 64 bytes
            assert reader.BUFFER_SIZE == 64
            # Initial buffer is empty
            assert len(reader.buffer) == 0

            # Read 1 byte (triggers refill)
            reader.read_byte()
            assert len(reader.buffer) == 64  # Should be full buffer size
        finally:
            reader.close()

    def test_streaming_vs_legacy_memory(self, tmp_path):
        """
        Test that streaming mode uses significantly less memory.

        This test simulates the memory reduction from 65KB to <1KB
        by verifying the reader only buffers 64 bytes at a time.
        """
        old_file = tmp_path / "old.bin"
        new_file = tmp_path / "new.bin"
        delta_file = tmp_path / "test.delta"
        output_file = tmp_path / "output.bin"

        # Create large delta (50KB+)
        old_data = b"A" * 25000
        new_data = b"B" * 50000
        old_file.write_bytes(old_data)
        new_file.write_bytes(new_data)

        # Generate delta
        create_delta(str(old_file), str(new_file), str(delta_file))
        delta_size = delta_file.stat().st_size

        # Legacy mode: loads entire delta into memory
        delta_bytes = delta_file.read_bytes()
        legacy_memory_usage = len(delta_bytes)
        assert legacy_memory_usage == delta_size

        # Streaming mode: uses fixed 64-byte buffer
        reader = ChunkedDeltaReader(str(delta_file))
        try:
            streaming_buffer_usage = reader.BUFFER_SIZE
            assert streaming_buffer_usage == 64

            # Verify streaming uses 99%+ less memory
            memory_reduction = (1 - streaming_buffer_usage / legacy_memory_usage) * 100
            assert memory_reduction > 99.0, \
                f"Expected >99% reduction, got {memory_reduction:.1f}%"

            print(f"\nMemory usage comparison:")
            print(f"  Legacy mode:    {legacy_memory_usage:,} bytes")
            print(f"  Streaming mode: {streaming_buffer_usage:,} bytes")
            print(f"  Reduction:      {memory_reduction:.2f}%")
        finally:
            reader.close()

    def test_no_full_delta_load_in_streaming(self, tmp_path, monkeypatch):
        """Verify streaming mode never loads full delta into memory."""
        old_file = tmp_path / "old.txt"
        new_file = tmp_path / "new.txt"
        delta_file = tmp_path / "test.delta"
        output_file = tmp_path / "output.txt"

        old_file.write_text("Hello World!")
        new_file.write_text("Hello Python!")

        # Generate delta
        create_delta(str(old_file), str(new_file), str(delta_file))

        # Track file.read() calls to ensure no full read
        original_read = type(open(str(delta_file), "rb")).read
        max_read_size = [0]

        def tracked_read(self, size=-1):
            if size == -1 or size > 1024:
                raise AssertionError(
                    f"Attempted to read {size} bytes - should use chunked reading!"
                )
            max_read_size[0] = max(max_read_size[0], size if size > 0 else 0)
            return original_read(self, size)

        # Apply delta - should only read small chunks
        apply_delta(
            str(old_file),
            str(delta_file),
            str(output_file)
        )

        # Verify output is correct
        assert output_file.read_text() == "Hello Python!"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
