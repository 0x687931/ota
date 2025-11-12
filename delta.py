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


def _read_varint_from_reader(reader):
    """Read variable-length integer from ChunkedDeltaReader."""
    result = 0
    shift = 0
    while True:
        byte = reader.read_byte()
        if byte is None:
            raise DeltaError("Unexpected EOF reading varint")
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            break
        shift += 7
        if shift > 28:
            raise DeltaError("Varint too large")
    return result


class ChunkedDeltaReader:
    """Streaming delta reader with fixed 64-byte buffer."""

    BUFFER_SIZE = 64

    def __init__(self, delta_path):
        self.f = open(delta_path, "rb")
        self.buffer = bytearray()
        self.buffer_pos = 0
        self.eof = False

    def _refill_buffer(self):
        if self.buffer_pos >= len(self.buffer) and not self.eof:
            chunk = self.f.read(self.BUFFER_SIZE)
            if not chunk:
                self.eof = True
                return False
            self.buffer = bytearray(chunk)
            self.buffer_pos = 0
            return True
        return self.buffer_pos < len(self.buffer)

    def read_byte(self):
        if self.buffer_pos >= len(self.buffer):
            if not self._refill_buffer():
                return None
        byte = self.buffer[self.buffer_pos]
        self.buffer_pos += 1
        return byte

    def read_bytes(self, n):
        result = bytearray()
        while len(result) < n:
            if self.buffer_pos >= len(self.buffer):
                if not self._refill_buffer():
                    raise DeltaError("Unexpected EOF reading {} bytes".format(n))
            available = min(n - len(result), len(self.buffer) - self.buffer_pos)
            result.extend(self.buffer[self.buffer_pos:self.buffer_pos + available])
            self.buffer_pos += available
        return bytes(result)

    def close(self):
        if self.f:
            self.f.close()
            self.f = None


def _write_varint(value):
    """Write variable-length integer."""
    result = bytearray()
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value & 0x7F)
    return bytes(result)


def _apply_delta_legacy(old_path, delta_data, output_path, expected_hash=None, chunk_size=512):
    """
    Apply binary delta to create new file (legacy bytes-based implementation).

    Args:
        old_path: Path to original file
        delta_data: Delta instructions (bytes)
        output_path: Path for output file
        expected_hash: Expected SHA256 of result (optional)
        chunk_size: Read/write chunk size

    Returns:
        SHA256 hash of output file

    Raises:
        DeltaError: If delta is invalid or output hash mismatches
    """
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


def apply_delta(old_path, delta_data_or_path, output_path, expected_hash=None, chunk_size=512):
    """
    Apply binary delta to create new file.

    Supports both streaming (file path) and legacy (bytes) modes.

    Args:
        old_path: Path to original file
        delta_data_or_path: Delta file path (str) or delta instructions (bytes)
        output_path: Path for output file
        expected_hash: Expected SHA256 of result (optional)
        chunk_size: Read/write chunk size

    Returns:
        SHA256 hash of output file

    Raises:
        DeltaError: If delta is invalid or output hash mismatches
    """
    # Auto-detect mode: bytes = legacy, str = streaming
    if isinstance(delta_data_or_path, bytes):
        return _apply_delta_legacy(old_path, delta_data_or_path, output_path, expected_hash, chunk_size)

    # Streaming mode using ChunkedDeltaReader
    delta_path = delta_data_or_path
    reader = None

    try:
        reader = ChunkedDeltaReader(delta_path)

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
                opcode = reader.read_byte()
                if opcode is None:
                    raise DeltaError("Unexpected EOF reading opcode")

                if opcode == OP_END:
                    break

                elif opcode == OP_COPY_OLD:
                    # Read copy position and length
                    copy_offset = _read_varint_from_reader(reader)
                    copy_length = _read_varint_from_reader(reader)

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
                    insert_length = _read_varint_from_reader(reader)

                    if insert_length > MAX_INSERT_SIZE:
                        raise DeltaError("Insert size too large: {}".format(insert_length))

                    # Stream insert data in chunks
                    remaining = insert_length
                    while remaining > 0:
                        chunk = min(chunk_size, remaining)
                        data = reader.read_bytes(chunk)
                        new_file.write(data)
                        output_hash.update(data)
                        remaining -= chunk

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

    finally:
        if reader:
            reader.close()


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
