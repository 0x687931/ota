"""
Lightweight binary diff/patch system for MicroPython OTA updates.

Uses a simple instruction-based format optimized for low memory usage.
Designed for embedded systems with limited RAM (RP2040 ~200KB).
"""

import os
import sys

try:
    import uhashlib as hashlib
except Exception:
    import hashlib

MICROPYTHON = sys.implementation.name == "micropython"

# Delta format constants
DELTA_MAGIC = b'OTADELTA'
DELTA_VERSION = 1

# Instruction opcodes
OP_COPY_OLD = 0x01  # Copy bytes from old file
OP_NEW_DATA = 0x02  # Insert new data
OP_END = 0xFF       # End of delta

# Maximum chunk sizes to prevent memory exhaustion
MAX_COPY_SIZE = 4096
MAX_INSERT_SIZE = 2048


class DeltaError(Exception):
    """Delta operation error."""
    pass


class _ChunkedDeltaReader:
    """
    Streaming delta reader with minimal lookahead buffer.
    Reduces memory usage from O(delta_size) to O(buffer_size).
    """
    def __init__(self, file_handle, buffer_size=64):
        self.f = file_handle
        self.buffer = bytearray()
        self.buffer_size = buffer_size
        self.offset = 0
        self.eof = False

    def _ensure_bytes(self, count):
        """Ensure at least 'count' bytes in buffer."""
        while len(self.buffer) < count and not self.eof:
            chunk = self.f.read(self.buffer_size)
            if not chunk:
                self.eof = True
                break
            self.buffer.extend(chunk)

    def read_byte(self):
        """Read single byte."""
        self._ensure_bytes(1)
        if len(self.buffer) < 1:
            raise DeltaError("Unexpected EOF reading delta")
        b = self.buffer[0]
        del self.buffer[0]
        self.offset += 1
        return b

    def read_bytes(self, count):
        """Read exact number of bytes."""
        result = bytearray()
        while count > 0:
            self._ensure_bytes(count)
            available = min(len(self.buffer), count)
            if available == 0:
                raise DeltaError("Unexpected EOF reading delta")
            result.extend(self.buffer[:available])
            del self.buffer[:available]
            self.offset += available
            count -= available
        return bytes(result)

    def read_varint(self):
        """Read variable-length integer."""
        result = 0
        shift = 0
        while True:
            byte = self.read_byte()
            result |= (byte & 0x7F) << shift
            if not (byte & 0x80):
                break
            shift += 7
        return result


def _read_varint(data, offset):
    """Read variable-length integer (up to 32-bit)."""
    result = 0
    shift = 0
    while offset < len(data):
        byte = data[offset]
        offset += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            break
        shift += 7
    return result, offset


def _write_varint(value):
    """Write variable-length integer."""
    result = bytearray()
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value & 0x7F)
    return bytes(result)


def apply_delta(old_path, delta_data, output_path, expected_hash=None, chunk_size=512):
    """
    Apply binary delta to create new file.

    Args:
        old_path: Path to original file
        delta_data: Delta instructions (bytes or file path string for streaming)
        output_path: Path for output file
        expected_hash: Expected SHA256 of result (optional)
        chunk_size: Read/write chunk size

    Returns:
        SHA256 hash of output file

    Raises:
        DeltaError: If delta is invalid or output hash mismatches
    """
    # Support both bytes (legacy) and file path (streaming)
    if isinstance(delta_data, (str, bytes)) and not isinstance(delta_data, bytes):
        # String path: use streaming mode
        with open(delta_data, "rb") as delta_file:
            return _apply_delta_streaming(old_path, delta_file, output_path, expected_hash, chunk_size)
    else:
        # Bytes: use legacy in-memory mode (backward compatibility)
        return _apply_delta_legacy(old_path, delta_data, output_path, expected_hash, chunk_size)


def _apply_delta_streaming(old_path, delta_file, output_path, expected_hash=None, chunk_size=512):
    """Apply delta using streaming reader (low memory)."""
    reader = _ChunkedDeltaReader(delta_file, buffer_size=64)

    # Verify delta header
    magic = reader.read_bytes(8)
    if magic != DELTA_MAGIC:
        raise DeltaError("Invalid delta magic")

    version = reader.read_byte()
    if version != DELTA_VERSION:
        raise DeltaError("Unsupported delta version: {}".format(version))

    # Process delta instructions
    output_hash = hashlib.sha256()

    with open(old_path, "rb") as old_file, \
         open(output_path, "wb") as new_file:

        while True:
            try:
                opcode = reader.read_byte()
            except DeltaError:
                # EOF without OP_END is an error, but handled below
                break

            if opcode == OP_END:
                break

            elif opcode == OP_COPY_OLD:
                # Read copy position and length
                copy_offset = reader.read_varint()
                copy_length = reader.read_varint()

                if copy_length > MAX_COPY_SIZE:
                    raise DeltaError("Copy size too large: {}".format(copy_length))

                # Copy from old file in chunks
                old_file.seek(copy_offset)
                remaining = copy_length
                while remaining > 0:
                    chunk = min(chunk_size, remaining)
                    data = old_file.read(chunk)
                    if len(data) != chunk:
                        raise DeltaError("Unexpected EOF in old file")
                    new_file.write(data)
                    output_hash.update(data)
                    remaining -= chunk

            elif opcode == OP_NEW_DATA:
                # Read insert length and data
                insert_length = reader.read_varint()

                if insert_length > MAX_INSERT_SIZE:
                    raise DeltaError("Insert size too large: {}".format(insert_length))

                data = reader.read_bytes(insert_length)
                new_file.write(data)
                output_hash.update(data)

            else:
                raise DeltaError("Unknown opcode: 0x{:02x}".format(opcode))

        new_file.flush()
        if hasattr(os, "fsync"):
            os.fsync(new_file.fileno())

    # Verify output hash if provided
    result_hash = output_hash.hexdigest() if hasattr(output_hash, 'hexdigest') else \
                  __import__('binascii').hexlify(output_hash.digest()).decode()

    if expected_hash and result_hash != expected_hash:
        raise DeltaError("Output hash mismatch: expected {}, got {}".format(
            expected_hash, result_hash))

    return result_hash


def _apply_delta_legacy(old_path, delta_data, output_path, expected_hash=None, chunk_size=512):
    """Apply delta using in-memory buffer (backward compatibility)."""
    # Verify delta header
    if len(delta_data) < 9:
        raise DeltaError("Delta too short")

    if delta_data[:8] != DELTA_MAGIC:
        raise DeltaError("Invalid delta magic")

    version = delta_data[8]
    if version != DELTA_VERSION:
        raise DeltaError("Unsupported delta version: {}".format(version))

    # Process delta instructions
    offset = 9
    output_hash = hashlib.sha256()

    with open(old_path, "rb") as old_file, \
         open(output_path, "wb") as new_file:

        while offset < len(delta_data):
            opcode = delta_data[offset]
            offset += 1

            if opcode == OP_END:
                break

            elif opcode == OP_COPY_OLD:
                # Read copy position and length
                copy_offset, offset = _read_varint(delta_data, offset)
                copy_length, offset = _read_varint(delta_data, offset)

                if copy_length > MAX_COPY_SIZE:
                    raise DeltaError("Copy size too large: {}".format(copy_length))

                # Copy from old file in chunks
                old_file.seek(copy_offset)
                remaining = copy_length
                while remaining > 0:
                    chunk = min(chunk_size, remaining)
                    data = old_file.read(chunk)
                    if len(data) != chunk:
                        raise DeltaError("Unexpected EOF in old file")
                    new_file.write(data)
                    output_hash.update(data)
                    remaining -= chunk

            elif opcode == OP_NEW_DATA:
                # Read insert length and data
                insert_length, offset = _read_varint(delta_data, offset)

                if insert_length > MAX_INSERT_SIZE:
                    raise DeltaError("Insert size too large: {}".format(insert_length))

                if offset + insert_length > len(delta_data):
                    raise DeltaError("Delta truncated")

                data = delta_data[offset:offset + insert_length]
                offset += insert_length
                new_file.write(data)
                output_hash.update(data)

            else:
                raise DeltaError("Unknown opcode: 0x{:02x}".format(opcode))

        new_file.flush()
        if hasattr(os, "fsync"):
            os.fsync(new_file.fileno())

    # Verify output hash if provided
    result_hash = output_hash.hexdigest() if hasattr(output_hash, 'hexdigest') else \
                  __import__('binascii').hexlify(output_hash.digest()).decode()

    if expected_hash and result_hash != expected_hash:
        raise DeltaError("Output hash mismatch: expected {}, got {}".format(
            expected_hash, result_hash))

    return result_hash


def create_delta(old_path, new_path, output_path=None, block_size=512):
    """
    Create binary delta between two files.

    Simple block-based diff algorithm suitable for embedded systems.
    Not as efficient as bsdiff but much simpler and lower memory.

    Args:
        old_path: Path to original file
        new_path: Path to new file
        output_path: Path for delta file (optional)
        block_size: Block size for matching

    Returns:
        Delta data as bytes
    """
    # Read both files
    with open(old_path, "rb") as f:
        old_data = f.read()
    with open(new_path, "rb") as f:
        new_data = f.read()

    # Build hash table of old file blocks
    old_blocks = {}
    for i in range(0, len(old_data), block_size):
        block = old_data[i:i + block_size]
        block_hash = hashlib.sha256(block).digest()[:8]  # Use first 8 bytes
        if block_hash not in old_blocks:
            old_blocks[block_hash] = []
        old_blocks[block_hash].append(i)

    # Generate delta instructions
    delta = bytearray(DELTA_MAGIC)
    delta.append(DELTA_VERSION)

    new_pos = 0
    pending_insert = bytearray()

    def flush_insert():
        """Flush pending insert data."""
        nonlocal pending_insert
        if pending_insert:
            delta.append(OP_NEW_DATA)
            delta.extend(_write_varint(len(pending_insert)))
            delta.extend(pending_insert)
            pending_insert = bytearray()

    while new_pos < len(new_data):
        # Try to find matching block in old file
        match_found = False
        if new_pos + block_size <= len(new_data):
            block = new_data[new_pos:new_pos + block_size]
            block_hash = hashlib.sha256(block).digest()[:8]

            if block_hash in old_blocks:
                # Found matching block, use COPY instruction
                old_pos = old_blocks[block_hash][0]

                # Extend match as far as possible
                match_len = block_size
                while (new_pos + match_len < len(new_data) and
                       old_pos + match_len < len(old_data) and
                       new_data[new_pos + match_len] == old_data[old_pos + match_len]):
                    match_len += 1

                # Flush any pending inserts first
                flush_insert()

                # Add copy instruction
                delta.append(OP_COPY_OLD)
                delta.extend(_write_varint(old_pos))
                delta.extend(_write_varint(match_len))

                new_pos += match_len
                match_found = True

        if not match_found:
            # No match found, accumulate for insert
            pending_insert.append(new_data[new_pos])
            new_pos += 1

            # Flush if insert buffer getting large
            if len(pending_insert) >= MAX_INSERT_SIZE:
                flush_insert()

    # Flush any remaining insert data
    flush_insert()

    # Add end marker
    delta.append(OP_END)

    delta_bytes = bytes(delta)

    # Write to file if path provided
    if output_path:
        with open(output_path, "wb") as f:
            f.write(delta_bytes)

    return delta_bytes


def estimate_delta_size(old_path, new_path, block_size=512):
    """
    Estimate delta size without creating full delta.
    Useful for deciding whether to use delta or full download.

    Returns:
        Estimated delta size in bytes
    """
    with open(old_path, "rb") as f:
        old_size = len(f.read())
    with open(new_path, "rb") as f:
        new_size = len(f.read())

    # Very rough estimate: assume 30% match rate for typical code changes
    # Header + estimated instructions
    estimated = 9  # Header
    estimated += new_size * 0.7  # 70% new data
    estimated += (new_size * 0.3) // block_size * 10  # Copy instructions

    return int(estimated)
