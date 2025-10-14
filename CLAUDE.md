# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MicroPython OTA (Over-The-Air) updater for embedded devices, particularly Raspberry Pi Pico W. The system enables secure firmware updates from GitHub repositories with dual-channel support (stable releases and development branches), streaming downloads, atomic file swaps, and automatic rollback.

## Core Architecture

### Main Components

- **ota.py** (1034 lines): Core `OTA` class implementing the update logic
  - Network connectivity (Wi-Fi for MicroPython)
  - GitHub API integration for release/branch resolution
  - Streaming download with Git blob SHA1 verification
  - Atomic file staging and swap with rollback support
  - Adaptive resource management (memory, storage, network conditions)
  - Path filtering with allow/ignore rules

- **main.py**: Entry point with configuration loader
  - Supports JSON, YAML (.yaml/.yml), and TOML (.toml) config files
  - Loads `ota_config.json` by default

- **manifest_gen.py**: Development tool to generate signed manifests
  - Creates manifest.json with SHA256/CRC32 hashes for all included files
  - Supports HMAC-SHA256 signature generation for manifest verification

### Dual Platform Support

Code runs on both **MicroPython** (target) and **CPython** (development/testing):
- Detection via `sys.implementation.name == "micropython"`
- Conditional imports: `ujson`/`json`, `uhashlib`/`hashlib`, `urequests`/`requests`, `ubinascii`/`binascii`
- CPython stubs for MicroPython-specific modules (`network`, `machine`)

### Update Channels

1. **Stable**: Pulls from latest GitHub release (requires tag)
   - Can use manifest-based (manifest.json asset) or manifestless (Git tree) mode
   - Manifest includes SHA256/CRC32 verification and optional signature

2. **Developer**: Pulls from branch tip (e.g., `main`)
   - Always manifestless mode using Git tree API
   - Downloads files matching allow/ignore filters

### Path Filtering System

Critical security feature enforced throughout ota.py:
- `allow`: Whitelist of exact files or directory prefixes (e.g., `["main.py", "lib/"]`)
- `ignore`: Blacklist (takes precedence over allow)
- Paths normalized: leading/trailing slashes stripped, `..` and absolute paths rejected
- Applied to: downloads, manifest entries, deletions, and swap operations
- See ota.py:320-332 (`_is_permitted`) and ota.py:629-635 (`_normalize_path`)

### File Operations Flow

1. **Staging** (`.ota_stage/` by default):
   - Files downloaded and verified here first
   - Git blob SHA1 computed during streaming (ota.py:645-697)

2. **Atomic Swap** (ota.py:769-847):
   - Existing files backed up to `.ota_backup/`
   - Staged files moved to final location
   - On error: automatic rollback from backup
   - Version state written to `version.json`

3. **Startup Cleanup** (ota.py:400-420):
   - On boot, restores any files in backup dir (indicates incomplete previous update)
   - Clears staging directory

### Resource Management

The OTA class adapts to device constraints:
- **Memory**: Reduces chunk size if free RAM < threshold (ota.py:371-387)
- **Storage**: Checks available space before download (requires 2x update size)
- **Network**: Adjusts retries/backoff based on Wi-Fi RSSI signal strength (ota.py:442-461)
- All adaptations logged in debug mode

## Common Commands

### Testing

```bash
# Run full test suite (CPython required)
pytest

# Run integration test (dry-run channel resolution)
python integration_test.py

# Run specific test file
pytest tests/test_path_filtering.py

# Run with verbose output
pytest -v
```

### Manifest Generation

```bash
# Generate manifest for specific files
python manifest_gen.py --version v1.0.0 --include "*.py" "lib/**/*.py"

# Generate with signature (requires MANIFEST_KEY env var)
export MANIFEST_KEY="your-secret-key"
python manifest_gen.py --version v1.0.0 --key "$MANIFEST_KEY"

# Use file list
python manifest_gen.py --file-list files.txt --version v1.0.0
```

### On-Device Usage (MicroPython REPL)

```python
# Basic update check
import main
main.main()

# Manual control
from ota import OTA
cfg = main.load_config("ota_config.json")
ota = OTA(cfg)
ota.update_if_available()
```

## Configuration

Configuration file (ota_config.json/yaml/toml) controls all behavior:

**Required fields:**
- `owner`, `repo`: GitHub repository
- `ssid`, `password`: Wi-Fi credentials (MicroPython only)
- `channel`: `"stable"` or `"developer"`
- `allow`: List of paths/prefixes to update (e.g., `["main.py", "lib/"]`)

**Key optional fields:**
- `ignore`: Paths to exclude (takes precedence over allow)
- `branch`: Branch name for developer channel (default: "main")
- `token`: GitHub personal access token for private repos
- `force`: Set to `true` to bypass version check
- `chunk`: Download buffer size (default 1024, auto-adjusted by memory)
- `stage_dir`, `backup_dir`: Override staging/backup directories
- `reset_mode`: `"hard"` (default), `"soft"`, or `"none"`
- `debug`: Set to `true` for verbose logging
- `manifest_key`: Shared secret for manifest signature verification

See README.md lines 124-146 for complete field documentation.

## Important Implementation Details

### Hash Verification
- **Git blob SHA1**: Used for manifestless mode, computed as `sha1("blob " + size + "\0" + content)`
- **SHA256/CRC32**: Used for manifest-based mode
- Streaming verification prevents full file buffering (critical for memory-constrained devices)

### Network Error Handling
- Configurable retries and exponential backoff
- HTTP timeout handling differs between CPython (separate connect/read) and MicroPython (unified)
- RSSI-based adaptation: poor signal (<-75 dBm) increases retries/backoff automatically

### Filesystem Safety
- `os.sync()` and `f.flush()` + `os.fsync()` used throughout to ensure durability
- Atomic rename for final file placement
- Path validation prevents directory traversal attacks

### Testing Strategy
- Extensive pytest suite with monkeypatching for network/filesystem isolation
- Tests cover: path filtering, manifest parsing, hash verification, staging, swap, rollback, timeouts
- Integration test verifies GitHub API connectivity without applying changes

## File Structure Notes

```
ota.py              # Core updater (single-file deployment)
main.py             # Entry point and config loader
manifest_gen.py     # Development tool (not deployed to device)
ota_config.json     # Example configuration (customize before use)
manifest.json       # Generated manifest for stable releases
tests/              # pytest test suite (16 test modules)
integration_test.py # Channel resolution smoke test
```

The design prioritizes single-file deployment: `ota.py` is self-contained with no external dependencies beyond MicroPython stdlib.
